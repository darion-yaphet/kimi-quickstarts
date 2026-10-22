import json
import os
import shutil
import subprocess
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from kimi_local import sandbox as sandbox_module
from kimi_local.sandbox import SandboxExecutor


class SandboxExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.executor = SandboxExecutor(Path(self.temp.name) / "jobs.sqlite3")

    def test_submit_persists_job_and_audit_run(self):
        job = self.executor.submit("print('ok')", session_id="session-1")
        self.assertEqual(job["status"], "queued")
        self.assertEqual(self.executor.list_jobs("session-1")[0]["id"], job["id"])

        unavailable = {
            "available": False,
            "code": "docker_not_found",
            "message": "missing",
        }
        with patch.object(self.executor, "check_backend", return_value=unavailable):
            result = self.executor.run_job(job["id"])
        self.assertEqual(result["status"], "backend_unavailable")
        self.assertEqual(result["error_code"], "docker_not_found")
        self.assertEqual(result["runs"][0]["status"], "backend_unavailable")

        reopened = SandboxExecutor(Path(self.temp.name) / "jobs.sqlite3")
        self.assertEqual(reopened.get_job(job["id"])["status"], "backend_unavailable")

    def test_rejects_unsupported_language_and_large_source(self):
        with self.assertRaisesRegex(ValueError, "only Python"):
            self.executor.submit("console.log(1)", language="javascript")
        limited = SandboxExecutor(":memory:", max_source_chars=4)
        self.addCleanup(limited.close)
        with self.assertRaisesRegex(ValueError, "exceeds"):
            limited.submit("print(1)")
        for invalid in (0, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                SandboxExecutor(":memory:", timeout_seconds=invalid)
        with self.assertRaises(ValueError):
            SandboxExecutor(":memory:", lease_seconds=float("nan"))
        with self.assertRaises(ValueError):
            SandboxExecutor(":memory:", cpus="0")
        with self.assertRaises(ValueError):
            SandboxExecutor(":memory:", memory="0m")
        with self.assertRaises(ValueError):
            SandboxExecutor(":memory:", pids_limit=float("inf"))

    def test_command_has_fixed_isolation_and_source_is_not_an_argument(self):
        executor = SandboxExecutor(
            ":memory:",
            image="registry.example/python@sha256:abc123",
            memory="64m",
            cpus="0.25",
        )
        self.addCleanup(executor.close)
        source = "print('$(touch /tmp/host-pwned)'); __import__('os').system('id')"
        command = executor._command("container-name")
        self.assertNotIn(source, command)
        self.assertIn("--pull=never", command)
        self.assertIn("--interactive", command)
        self.assertIn("--network=none", command)
        self.assertIn("--read-only", command)
        self.assertIn("--cap-drop=ALL", command)
        self.assertIn("--security-opt=no-new-privileges", command)
        self.assertIn("--user=65534:65534", command)
        self.assertIn("--memory=64m", command)
        self.assertIn("--cpus=0.25", command)
        self.assertFalse(
            any(arg.startswith("--volume") or arg == "-v" for arg in command)
        )
        self.assertEqual(
            command[-4:-1], ["-I", "-c", sandbox_module._SUPERVISOR_SOURCE]
        )
        self.assertEqual(command[-1], "10")
        with self.assertRaisesRegex(ValueError, "fixed Docker image"):
            SandboxExecutor(":memory:", image="--privileged")
        with self.assertRaisesRegex(ValueError, "fixed Docker image"):
            SandboxExecutor(":memory:", image="python:latest --network=host")

        labeled = executor._command("container-name", "job_1", 2, "owner-token")
        self.assertIn("kimi.sandbox.job=job_1", labeled)
        self.assertIn("kimi.sandbox.attempt=2", labeled)
        self.assertIn("kimi.sandbox.owner=owner-token", labeled)

    def test_constant_supervisor_executes_stdin_and_enforces_watchdog(self):
        completed = subprocess.run(
            [sys.executable, "-I", "-c", sandbox_module._SUPERVISOR_SOURCE, "1"],
            input=b"print('supervised')",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=2,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), b"supervised")

        timed_out = subprocess.run(
            [sys.executable, "-I", "-c", sandbox_module._SUPERVISOR_SOURCE, "0.05"],
            input=b"import time; time.sleep(5)",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=1,
            check=False,
        )
        self.assertEqual(timed_out.returncode, 124)
        self.assertIn(b"watchdog timeout", timed_out.stderr)

    def test_output_limit_kills_and_cleans_up(self):
        executor = SandboxExecutor(":memory:", max_output_bytes=3)
        self.addCleanup(executor.close)
        job = executor.submit("print('too much')")
        process = Mock()
        process.poll.return_value = None
        process.returncode = None
        with (
            patch.object(executor, "check_backend", return_value={"available": True}),
            patch("kimi_local.sandbox.subprocess.Popen", return_value=process),
            patch.object(
                executor, "_collect", return_value=(b"abc", b"", "output_limit")
            ),
            patch.object(
                executor,
                "_cleanup",
                return_value={"ok": True, "code": "removed", "message": "removed"},
            ) as cleanup,
        ):
            result = executor.run_job(job["id"])
        process.kill.assert_called_once()
        cleanup.assert_called_once()
        self.assertEqual(result["status"], "output_limited")
        self.assertEqual(result["stdout"], "abc")

    def test_running_job_cannot_be_claimed_twice(self):
        job = self.executor.submit("print('once')")
        self.executor._set_running(job["id"], "kimi-sandbox-" + "a" * 24)
        with self.assertRaisesRegex(RuntimeError, "active or unresolved"):
            self.executor._set_running(job["id"], "kimi-sandbox-" + "b" * 24)
        result = self.executor.get_job(job["id"])
        self.assertEqual(result["status"], "running")
        self.assertEqual(len(result["runs"]), 1)

    def test_owner_heartbeat_renews_persisted_lease(self):
        job = self.executor.submit("print('live')")
        attempt, owner = self.executor._set_running(
            job["id"], "kimi-sandbox-" + "7" * 24
        )
        before = self.executor.get_job(job["id"])
        with patch(
            "kimi_local.sandbox._after", return_value="2999-01-01T00:00:00+00:00"
        ):
            self.executor._heartbeat(job["id"], attempt, owner)
        after = self.executor.get_job(job["id"])
        self.assertNotEqual(before["heartbeat_at"], after["heartbeat_at"])
        self.assertEqual(after["lease_expires_at"], "2999-01-01T00:00:00+00:00")
        self.assertEqual(
            after["runs"][0]["lease_expires_at"], "2999-01-01T00:00:00+00:00"
        )

    def test_timeout_kills_and_cleans_up(self):
        executor = SandboxExecutor(":memory:", timeout_seconds=0.01)
        self.addCleanup(executor.close)
        job = executor.submit("while True: pass")
        process = Mock()
        process.poll.return_value = None
        process.returncode = None
        with (
            patch.object(executor, "check_backend", return_value={"available": True}),
            patch("kimi_local.sandbox.subprocess.Popen", return_value=process),
            patch.object(executor, "_collect", return_value=(b"", b"", "timeout")),
            patch.object(
                executor,
                "_cleanup",
                return_value={"ok": True, "code": "removed", "message": "removed"},
            ) as cleanup,
        ):
            result = executor.run_job(job["id"])
        process.kill.assert_called_once()
        cleanup.assert_called_once()
        self.assertEqual(result["status"], "timed_out")
        self.assertEqual(result["error_code"], "timeout")

    def test_cleanup_refusal_is_persisted_instead_of_reporting_timeout(self):
        executor = SandboxExecutor(":memory:", timeout_seconds=0.01)
        self.addCleanup(executor.close)
        job = executor.submit("while True: pass")
        process = Mock()
        process.poll.return_value = None
        with (
            patch.object(executor, "check_backend", return_value={"available": True}),
            patch("kimi_local.sandbox.subprocess.Popen", return_value=process),
            patch.object(executor, "_collect", return_value=(b"", b"", "timeout")),
            patch.object(
                executor,
                "_cleanup",
                return_value={
                    "ok": False,
                    "code": "container_identity_mismatch",
                    "message": "wrong owner",
                },
            ),
        ):
            result = executor.run_job(job["id"])
        self.assertEqual(result["status"], "cleanup_failed")
        self.assertEqual(result["error_code"], "container_identity_mismatch")
        self.assertIn("not confirmed", result["error_message"])
        self.assertIsNotNone(result["container_name"])
        self.assertIsNotNone(result["owner_token"])
        with (
            patch.object(executor, "check_backend", return_value={"available": True}),
            self.assertRaisesRegex(RuntimeError, "unresolved execution"),
        ):
            executor.run_job(job["id"])
        with patch.object(
            executor,
            "_cleanup",
            return_value={"ok": True, "code": "removed", "message": "removed"},
        ):
            recovered = executor.recover_orphans()
        self.assertEqual(recovered[0]["status"], "failed")
        self.assertIsNone(recovered[0]["container_name"])

    def test_cleanup_validates_identity_before_deletion(self):
        with (
            patch.object(
                self.executor,
                "_inspect_container",
                return_value={
                    "state": "present",
                    "labels": {
                        "kimi.sandbox.job": "another-job",
                        "kimi.sandbox.attempt": "1",
                        "kimi.sandbox.owner": "another-owner",
                    },
                },
            ),
            patch("kimi_local.sandbox.subprocess.run") as run,
        ):
            result = self.executor._cleanup(
                "kimi-sandbox-" + "c" * 24, "job_expected", 1, "expected-owner"
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "container_identity_mismatch")
        run.assert_not_called()

    def test_cleanup_reports_docker_removal_failure(self):
        labels = {
            "kimi.sandbox.job": "job_expected",
            "kimi.sandbox.attempt": "1",
            "kimi.sandbox.owner": "expected-owner",
        }
        removal = Mock(returncode=1, stderr="daemon refused removal")
        with (
            patch.object(
                self.executor,
                "_inspect_container",
                return_value={"state": "present", "labels": labels},
            ),
            patch("kimi_local.sandbox.subprocess.run", return_value=removal),
            patch("kimi_local.sandbox.time.monotonic", side_effect=[0, 3]),
        ):
            result = self.executor._cleanup(
                "kimi-sandbox-" + "c" * 24, "job_expected", 1, "expected-owner"
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "container_remove_failed")
        self.assertIn("refused", result["message"])

    def test_cleanup_accepts_removal_in_progress_after_absence_is_confirmed(self):
        labels = {
            "kimi.sandbox.job": "job_expected",
            "kimi.sandbox.attempt": "1",
            "kimi.sandbox.owner": "expected-owner",
        }
        removal = Mock(returncode=1, stderr="removal already in progress")
        with (
            patch.object(
                self.executor,
                "_inspect_container",
                side_effect=[
                    {"state": "present", "labels": labels},
                    {
                        "state": "absent",
                        "code": "already_absent",
                        "message": "No such container",
                    },
                ],
            ),
            patch("kimi_local.sandbox.subprocess.run", return_value=removal),
        ):
            result = self.executor._cleanup(
                "kimi-sandbox-" + "c" * 24, "job_expected", 1, "expected-owner"
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["code"], "removed")

    def test_recover_orphans_closes_expired_attempt(self):
        job = self.executor.submit("print('abandoned')")
        container_name = "kimi-sandbox-" + "d" * 24
        with patch(
            "kimi_local.sandbox._after", return_value="2000-01-01T00:00:00+00:00"
        ):
            self.executor._set_running(job["id"], container_name)
        with patch.object(
            self.executor,
            "_cleanup",
            return_value={"ok": True, "code": "removed", "message": "removed"},
        ) as cleanup:
            recovered = self.executor.recover_orphans()
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0]["status"], "failed")
        self.assertEqual(recovered[0]["error_code"], "owner_lease_expired")
        self.assertEqual(recovered[0]["runs"][0]["status"], "failed")
        cleanup.assert_called_once()

    def test_recover_orphans_protects_live_owner(self):
        job = self.executor.submit("print('still live')")
        self.executor._set_running(job["id"], "kimi-sandbox-" + "e" * 24)
        with patch.object(self.executor, "_cleanup") as cleanup:
            recovered = self.executor.recover_orphans()
        self.assertEqual(recovered, [])
        cleanup.assert_not_called()
        self.assertEqual(self.executor.get_job(job["id"])["status"], "running")

    def test_recovery_claim_blocks_a_new_attempt(self):
        job = self.executor.submit("print('recovering')")
        container_name = "kimi-sandbox-" + "9" * 24
        with patch(
            "kimi_local.sandbox._after", return_value="2000-01-01T00:00:00+00:00"
        ):
            _, owner = self.executor._set_running(job["id"], container_name)
        claimed = self.executor._claim_orphan(
            job["id"], owner, "2001-01-01T00:00:00+00:00"
        )
        self.assertIsNotNone(claimed)
        with self.assertRaisesRegex(RuntimeError, "active or unresolved"):
            self.executor._set_running(job["id"], "kimi-sandbox-" + "8" * 24)

    def test_expired_recovery_lease_can_be_reclaimed(self):
        job = self.executor.submit("print('abandoned twice')")
        container_name = "kimi-sandbox-" + "f" * 24
        with patch(
            "kimi_local.sandbox._after", return_value="2000-01-01T00:00:00+00:00"
        ):
            _, original_owner = self.executor._set_running(job["id"], container_name)
            claimed = self.executor._claim_orphan(
                job["id"], original_owner, "2001-01-01T00:00:00+00:00"
            )
        self.assertIsNotNone(claimed)
        with patch.object(
            self.executor,
            "_cleanup",
            return_value={"ok": True, "code": "removed", "message": "removed"},
        ) as cleanup:
            recovered = self.executor.recover_orphans()
        self.assertEqual(recovered[0]["error_code"], "owner_lease_expired")
        cleanup.assert_called_once_with(container_name, job["id"], 1, original_owner)

    def test_nonzero_exit_is_persisted(self):
        job = self.executor.submit("raise RuntimeError('boom')")
        process = Mock(returncode=7)
        with (
            patch.object(
                self.executor, "check_backend", return_value={"available": True}
            ),
            patch("kimi_local.sandbox.subprocess.Popen", return_value=process),
            patch.object(self.executor, "_collect", return_value=(b"", b"boom", None)),
        ):
            result = self.executor.run_job(job["id"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["exit_code"], 7)
        self.assertEqual(result["error_code"], "nonzero_exit")
        self.assertEqual(result["runs"][0]["stderr"], "boom")

    def test_capabilities_reflect_backend(self):
        with patch.object(
            self.executor,
            "check_backend",
            return_value={"available": False, "code": "docker_not_found"},
        ):
            capabilities = self.executor.capabilities()
        self.assertFalse(capabilities["available"])
        self.assertEqual(capabilities["languages"], ["python"])
        self.assertEqual(capabilities["image_pull_policy"], "never")
        self.assertFalse(capabilities["isolation"]["host_environment"])

    def test_backend_check_requires_prepared_image_without_pulling(self):
        daemon = Mock(returncode=0, stdout="27.0", stderr="")
        missing_image = Mock(returncode=1, stderr="missing")
        with (
            patch("kimi_local.sandbox.shutil.which", return_value="/usr/bin/docker"),
            patch(
                "kimi_local.sandbox.subprocess.run", side_effect=[daemon, missing_image]
            ) as run,
        ):
            backend = self.executor.check_backend()
        self.assertFalse(backend["available"])
        self.assertEqual(backend["code"], "image_not_found")
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[1][:3], ["docker", "image", "inspect"])
        self.assertFalse(any("pull" in command for command in commands))

    def test_docker_client_environment_preserves_context_without_secrets(self):
        process = Mock(returncode=0)
        process.stdin = Mock(closed=False)
        process.stdout = Mock(closed=False)
        process.stderr = Mock(closed=False)
        job = self.executor.submit("print('ok')")
        client_environment = {
            "PATH": "/custom/bin",
            "HOME": "/users/test",
            "DOCKER_CONTEXT": "desktop-linux",
            "DOCKER_HOST": "unix:///custom/docker.sock",
            "ANTHROPIC_API_KEY": "secret-anthropic",
            "KIMI_API_KEY": "secret-kimi",
            "UNRELATED_SECRET": "secret-other",
        }
        with (
            patch.dict(os.environ, client_environment, clear=True),
            patch.object(
                self.executor, "check_backend", return_value={"available": True}
            ),
            patch("kimi_local.sandbox.subprocess.Popen", return_value=process) as popen,
            patch.object(self.executor, "_collect", return_value=(b"ok", b"", None)),
        ):
            self.executor.run_job(job["id"])
        forwarded = popen.call_args.kwargs["env"]
        self.assertEqual(forwarded["DOCKER_CONTEXT"], "desktop-linux")
        self.assertEqual(forwarded["DOCKER_HOST"], "unix:///custom/docker.sock")
        self.assertNotIn("ANTHROPIC_API_KEY", forwarded)
        self.assertNotIn("KIMI_API_KEY", forwarded)
        self.assertNotIn("UNRELATED_SECRET", forwarded)
        self.assertNotIn("-e", popen.call_args.args[0])
        process.stdin.close.assert_called_once()
        process.stdout.close.assert_called_once()
        process.stderr.close.assert_called_once()

    def test_stalled_stdin_is_covered_by_execution_deadline(self):
        executor = SandboxExecutor(":memory:", timeout_seconds=0.05)
        self.addCleanup(executor.close)
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            started = time.monotonic()
            _, _, reason = executor._collect(process, b"x" * (2 * 1024 * 1024), Mock())
            elapsed = time.monotonic() - started
        finally:
            executor._terminate_process(process)
        self.assertEqual(reason, "timeout")
        self.assertLess(elapsed, 1)


@unittest.skipUnless(
    os.environ.get("KIMI_TEST_DOCKER") == "1" and shutil.which("docker"),
    "set KIMI_TEST_DOCKER=1 with Docker available",
)
class DockerSandboxIntegrationTests(unittest.TestCase):
    def _executor(self, directory, **kwargs):
        return SandboxExecutor(
            Path(directory) / "jobs.sqlite3",
            image=os.environ.get("KIMI_SANDBOX_IMAGE", "python:3.12-alpine"),
            **kwargs,
        )

    def test_executes_python_in_real_container(self):
        with TemporaryDirectory() as directory:
            executor = self._executor(directory)
            if not executor.check_backend()["available"]:
                self.skipTest("Docker daemon is unavailable")
            job = executor.submit("print('sandbox-ok')")
            result = executor.run_job(job["id"])
            self.assertEqual(result["status"], "succeeded", result)
            self.assertEqual(result["stdout"].strip(), "sandbox-ok")

    def test_real_container_is_nonroot_readonly_and_offline(self):
        with TemporaryDirectory() as directory:
            executor = self._executor(directory)
            if not executor.check_backend()["available"]:
                self.skipTest("Docker daemon is unavailable")
            code = """
import json, os, socket
root_options = next(line.split()[3] for line in open('/proc/mounts') if line.split()[1] == '/')
readonly = 'ro' in root_options.split(',')
s = socket.socket()
network_errno = s.connect_ex(('1.1.1.1', 53))
s.close()
print(json.dumps({'uid': os.getuid(), 'readonly': readonly, 'network_errno': network_errno}))
"""
            result = executor.run_job(executor.submit(code)["id"])
            self.assertEqual(result["status"], "succeeded", result)
            evidence = json.loads(result["stdout"])
            self.assertNotEqual(evidence["uid"], 0)
            self.assertTrue(evidence["readonly"])
            self.assertNotEqual(evidence["network_errno"], 0)

    def test_real_timeout_and_output_limit_remove_owned_containers(self):
        with TemporaryDirectory() as directory:
            timeout_executor = self._executor(directory, timeout_seconds=0.2)
            if not timeout_executor.check_backend()["available"]:
                self.skipTest("Docker daemon is unavailable")
            timed_out = timeout_executor.run_job(
                timeout_executor.submit("import time; time.sleep(5)")["id"]
            )
            self.assertEqual(timed_out["status"], "timed_out", timed_out)
            timeout_container = timed_out["runs"][0]["container_name"]
            self.assertEqual(
                timeout_executor._inspect_container(timeout_container)["state"],
                "absent",
            )

        with TemporaryDirectory() as directory:
            output_executor = self._executor(directory, max_output_bytes=128)
            limited = output_executor.run_job(
                output_executor.submit("print('x' * 10000)")["id"]
            )
            self.assertEqual(limited["status"], "output_limited", limited)
            output_container = limited["runs"][0]["container_name"]
            self.assertEqual(
                output_executor._inspect_container(output_container)["state"], "absent"
            )


if __name__ == "__main__":
    unittest.main()
