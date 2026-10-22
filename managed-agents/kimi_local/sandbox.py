"""Persisted, Docker-backed execution sandbox for the local Kimi runtime.

Only Python source is accepted.  Source is passed over stdin to a disposable
container; it is never evaluated by the host Python process.
"""

from __future__ import annotations

import json
import math
import os
import re
import secrets
import selectors
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


_IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@+-]{0,254}$")
_RESOURCE_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)?[bkmgBKMG]?$|^[0-9]+(?:\.[0-9]+)?$")
_CONTAINER_RE = re.compile(r"^kimi-sandbox-[0-9a-f]{24}$")
_DOCKER_ENV_KEYS = {
    "PATH",
    "HOME",
    "DOCKER_HOST",
    "DOCKER_CONTEXT",
    "DOCKER_CONFIG",
    "DOCKER_CERT_PATH",
    "DOCKER_TLS_VERIFY",
    "XDG_RUNTIME_DIR",
}
_SUPERVISOR_SOURCE = r"""
import os
import signal
import subprocess
import sys
import time

deadline = time.monotonic() + float(sys.argv[1])
child = None

def expire(_signum, _frame):
    if child is not None and child.poll() is None:
        child.kill()
    raise TimeoutError

signal.signal(signal.SIGALRM, expire)
signal.setitimer(signal.ITIMER_REAL, max(0.001, deadline - time.monotonic()))
try:
    source = sys.stdin.buffer.read()
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError
    child = subprocess.Popen([sys.executable, "-I", "-"], stdin=subprocess.PIPE)
    try:
        child.communicate(source, timeout=remaining)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()
        raise TimeoutError
    exit_code = child.returncode
except TimeoutError:
    if child is not None:
        if child.poll() is None:
            child.kill()
        child.wait()
    sys.stderr.write("sandbox watchdog timeout\n")
    exit_code = 124
finally:
    signal.setitimer(signal.ITIMER_REAL, 0)
raise SystemExit(exit_code)
""".strip()
_TERMINAL_STATUSES = {
    "succeeded",
    "failed",
    "timed_out",
    "output_limited",
    "backend_unavailable",
    "cleanup_failed",
}


class _LeaseLost(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _after(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


class SandboxExecutor:
    """Queue, execute, and audit isolated Python jobs.

    Docker images are never pulled by execution.  Operators must prepare the
    configured image separately (for example, ``docker pull IMAGE``).
    """

    def __init__(
        self,
        database_path: str | Path,
        *,
        image: str = "python:3.12-alpine",
        docker_executable: str = "docker",
        timeout_seconds: float = 10,
        max_source_chars: int = 20_000,
        max_output_bytes: int = 64 * 1024,
        memory: str = "128m",
        cpus: str = "0.5",
        pids_limit: int = 64,
        tmpfs_size: str = "16m",
        lease_seconds: float = 15,
    ) -> None:
        if not _IMAGE_RE.fullmatch(image) or image.startswith("-"):
            raise ValueError("image must be a fixed Docker image reference")
        if not docker_executable or "\x00" in docker_executable:
            raise ValueError("docker_executable must be a valid executable name")
        if isinstance(timeout_seconds, bool) or isinstance(lease_seconds, bool):
            raise ValueError("timeout and lease must be finite positive numbers")
        try:
            timeout_value = float(timeout_seconds)
            lease_value = float(lease_seconds)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "timeout and lease must be finite positive numbers"
            ) from exc
        if not math.isfinite(timeout_value) or timeout_value <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not math.isfinite(lease_value) or lease_value <= 0:
            raise ValueError("lease_seconds must be positive")
        integer_limits = (max_source_chars, max_output_bytes, pids_limit)
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value <= 0
            for value in integer_limits
        ):
            raise ValueError("source, output, and process limits must be positive")
        for name, value in (
            ("memory", memory),
            ("cpus", cpus),
            ("tmpfs_size", tmpfs_size),
        ):
            if not _RESOURCE_RE.fullmatch(str(value)):
                raise ValueError(f"{name} has an invalid value")
            numeric = re.match(r"^[0-9]+(?:\.[0-9]+)?", str(value))
            numeric_value = float(numeric.group()) if numeric is not None else 0
            if not math.isfinite(numeric_value) or numeric_value <= 0:
                raise ValueError(f"{name} must be positive")

        self.database_path = str(database_path)
        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self.image = image
        self.docker_executable = docker_executable
        self.timeout_seconds = timeout_value
        self.max_source_chars = max_source_chars
        self.max_output_bytes = max_output_bytes
        self.memory = memory
        self.cpus = cpus
        self.pids_limit = pids_limit
        self.tmpfs_size = tmpfs_size
        self.lease_seconds = lease_value
        self._memory_connection: sqlite3.Connection | None = None
        if self.database_path == ":memory:":
            self._memory_connection = sqlite3.connect(
                ":memory:", check_same_thread=False
            )
            self._memory_connection.row_factory = sqlite3.Row
            self._memory_connection.execute("PRAGMA foreign_keys=ON")
        self._initialize()

    @staticmethod
    def _docker_env() -> dict[str, str]:
        """Return Docker client configuration without forwarding application secrets."""
        return {key: os.environ[key] for key in _DOCKER_ENV_KEYS if key in os.environ}

    def _connect(self) -> sqlite3.Connection:
        if self._memory_connection is not None:
            return self._memory_connection
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _close(self, connection: sqlite3.Connection) -> None:
        if connection is not self._memory_connection:
            connection.close()

    def close(self) -> None:
        """Close the retained connection used by an in-memory executor."""
        if self._memory_connection is not None:
            self._memory_connection.close()
            self._memory_connection = None

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sandbox_jobs (
                    id TEXT PRIMARY KEY,
                    session_id TEXT,
                    language TEXT NOT NULL,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    exit_code INTEGER,
                    stdout TEXT NOT NULL DEFAULT '',
                    stderr TEXT NOT NULL DEFAULT '',
                    error_code TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    owner_token TEXT,
                    lease_expires_at TEXT,
                    heartbeat_at TEXT,
                    container_name TEXT,
                    current_attempt INTEGER,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS sandbox_jobs_session_created
                    ON sandbox_jobs(session_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS sandbox_job_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES sandbox_jobs(id) ON DELETE CASCADE,
                    attempt INTEGER NOT NULL,
                    container_name TEXT NOT NULL,
                    image TEXT NOT NULL,
                    status TEXT NOT NULL,
                    exit_code INTEGER,
                    stdout TEXT NOT NULL DEFAULT '',
                    stderr TEXT NOT NULL DEFAULT '',
                    error_code TEXT,
                    error_message TEXT,
                    owner_token TEXT,
                    lease_expires_at TEXT,
                    heartbeat_at TEXT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    UNIQUE(job_id, attempt)
                );
                """
            )
            migrations = {
                "sandbox_jobs": {
                    "owner_token": "TEXT",
                    "lease_expires_at": "TEXT",
                    "heartbeat_at": "TEXT",
                    "container_name": "TEXT",
                    "current_attempt": "INTEGER",
                },
                "sandbox_job_runs": {
                    "stdout": "TEXT NOT NULL DEFAULT ''",
                    "stderr": "TEXT NOT NULL DEFAULT ''",
                    "owner_token": "TEXT",
                    "lease_expires_at": "TEXT",
                    "heartbeat_at": "TEXT",
                },
            }
            for table, columns in migrations.items():
                existing = {
                    row["name"]
                    for row in connection.execute(f"PRAGMA table_info({table})")
                }
                for column, definition in columns.items():
                    if column not in existing:
                        connection.execute(
                            f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
                        )
            connection.commit()
        finally:
            self._close(connection)

    @staticmethod
    def _job(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row else None

    def submit(
        self, code: str, language: str = "python", session_id: str | None = None
    ) -> dict[str, Any]:
        if language.lower() not in {"python", "python3", "py"}:
            raise ValueError("only Python execution is supported")
        if not isinstance(code, str) or not code.strip():
            raise ValueError("code must be a non-empty string")
        if len(code) > self.max_source_chars:
            raise ValueError(f"code exceeds {self.max_source_chars} characters")
        if session_id is not None and not isinstance(session_id, str):
            raise ValueError("session_id must be a string or None")

        job_id = f"job_{secrets.token_urlsafe(18)}"
        timestamp = _now()
        connection = self._connect()
        try:
            connection.execute(
                """
                INSERT INTO sandbox_jobs(
                    id, session_id, language, source, status, created_at, updated_at
                ) VALUES (?, ?, 'python', ?, 'queued', ?, ?)
                """,
                (job_id, session_id, code, timestamp, timestamp),
            )
            connection.commit()
        finally:
            self._close(connection)
        return self.get_job(job_id)  # type: ignore[return-value]

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM sandbox_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            job = self._job(row)
            if job is None:
                return None
            runs = connection.execute(
                "SELECT * FROM sandbox_job_runs WHERE job_id = ? ORDER BY attempt",
                (job_id,),
            ).fetchall()
            job["runs"] = [dict(run) for run in runs]
            return job
        finally:
            self._close(connection)

    def list_jobs(
        self, session_id: str | None = None, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        if limit <= 0 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        connection = self._connect()
        try:
            if session_id is None:
                rows = connection.execute(
                    "SELECT * FROM sandbox_jobs ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """SELECT * FROM sandbox_jobs WHERE session_id = ?
                       ORDER BY created_at DESC LIMIT ?""",
                    (session_id, limit),
                ).fetchall()
            return [dict(row) for row in rows]
        finally:
            self._close(connection)

    def capabilities(self) -> dict[str, Any]:
        backend = self.check_backend()
        return {
            "languages": ["python"],
            "backend": "docker",
            "available": backend["available"],
            "backend_detail": backend,
            "image": self.image,
            "image_pull_policy": "never",
            "limits": {
                "timeout_seconds": self.timeout_seconds,
                "max_source_chars": self.max_source_chars,
                "max_output_bytes": self.max_output_bytes,
                "memory": self.memory,
                "cpus": self.cpus,
                "pids": self.pids_limit,
                "lease_seconds": self.lease_seconds,
            },
            "isolation": {
                "network": "none",
                "read_only_root": True,
                "non_root": True,
                "host_mounts": False,
                "host_environment": False,
            },
        }

    def check_backend(self) -> dict[str, Any]:
        executable = shutil.which(self.docker_executable)
        if executable is None:
            return {
                "available": False,
                "code": "docker_not_found",
                "reason": f"{self.docker_executable!r} was not found",
                "message": f"{self.docker_executable!r} was not found",
                "image": self.image,
            }
        try:
            result = subprocess.run(
                [self.docker_executable, "version", "--format", "{{.Server.Version}}"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=3,
                check=False,
                text=True,
                env=self._docker_env(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {
                "available": False,
                "code": "docker_unavailable",
                "reason": str(exc),
                "message": str(exc),
                "image": self.image,
            }
        if result.returncode != 0:
            message = result.stderr.strip() or "Docker daemon is unavailable"
            return {
                "available": False,
                "code": "docker_unavailable",
                "reason": message,
                "message": message,
                "image": self.image,
            }
        try:
            image_result = subprocess.run(
                [self.docker_executable, "image", "inspect", self.image],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=3,
                check=False,
                text=True,
                env=self._docker_env(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {
                "available": False,
                "code": "docker_unavailable",
                "reason": str(exc),
                "message": str(exc),
                "image": self.image,
            }
        if image_result.returncode != 0:
            message = (
                f"Docker image {self.image!r} is not present; prepare it explicitly"
            )
            return {
                "available": False,
                "code": "image_not_found",
                "reason": message,
                "message": message,
                "image": self.image,
            }
        return {
            "available": True,
            "code": "ok",
            "reason": None,
            "message": "Docker daemon is available",
            "image": self.image,
            "version": result.stdout.strip(),
        }

    def _command(
        self,
        container_name: str,
        job_id: str | None = None,
        attempt: int | None = None,
        owner_token: str | None = None,
    ) -> list[str]:
        labels: list[str] = []
        if job_id is not None and attempt is not None and owner_token is not None:
            labels = [
                "--label",
                f"kimi.sandbox.job={job_id}",
                "--label",
                f"kimi.sandbox.attempt={attempt}",
                "--label",
                f"kimi.sandbox.owner={owner_token}",
            ]
        return [
            self.docker_executable,
            "run",
            "--rm",
            "--pull=never",
            "--interactive",
            "--name",
            container_name,
            *labels,
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            f"--memory={self.memory}",
            f"--cpus={self.cpus}",
            f"--pids-limit={self.pids_limit}",
            "--user=65534:65534",
            f"--tmpfs=/tmp:rw,noexec,nosuid,nodev,size={self.tmpfs_size}",
            self.image,
            "python",
            "-I",
            "-c",
            _SUPERVISOR_SOURCE,
            f"{self.timeout_seconds:.17g}",
        ]

    def _set_running(self, job_id: str, container_name: str) -> tuple[int, str]:
        timestamp = _now()
        owner_token = secrets.token_urlsafe(24)
        lease_expires_at = _after(self.lease_seconds)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM sandbox_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["status"] in {"running", "recovering", "cleanup_failed"}:
                raise RuntimeError(
                    f"job {job_id} has an active or unresolved execution"
                )
            attempt = connection.execute(
                "SELECT COALESCE(MAX(attempt), 0) + 1 FROM sandbox_job_runs WHERE job_id = ?",
                (job_id,),
            ).fetchone()[0]
            connection.execute(
                """UPDATE sandbox_jobs SET status='running', started_at=?, finished_at=NULL,
                   exit_code=NULL, stdout='', stderr='', error_code=NULL,
                   error_message=NULL, owner_token=?, lease_expires_at=?, heartbeat_at=?,
                   container_name=?, current_attempt=?, updated_at=? WHERE id=?""",
                (
                    timestamp,
                    owner_token,
                    lease_expires_at,
                    timestamp,
                    container_name,
                    attempt,
                    timestamp,
                    job_id,
                ),
            )
            connection.execute(
                """INSERT INTO sandbox_job_runs(
                    job_id, attempt, container_name, image, status, owner_token,
                    lease_expires_at, heartbeat_at, started_at
                ) VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?)""",
                (
                    job_id,
                    attempt,
                    container_name,
                    self.image,
                    owner_token,
                    lease_expires_at,
                    timestamp,
                    timestamp,
                ),
            )
            connection.commit()
            return int(attempt), owner_token
        except Exception:
            connection.rollback()
            raise
        finally:
            self._close(connection)

    def _finish(
        self,
        job_id: str,
        attempt: int,
        owner_token: str,
        *,
        status: str,
        exit_code: int | None = None,
        stdout: bytes = b"",
        stderr: bytes = b"",
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        if status not in _TERMINAL_STATUSES:
            raise ValueError("invalid terminal status")
        timestamp = _now()
        connection = self._connect()
        try:
            values = (
                status,
                exit_code,
                stdout.decode("utf-8", errors="replace"),
                stderr.decode("utf-8", errors="replace"),
                error_code,
                error_message,
                timestamp,
            )
            if status == "cleanup_failed":
                cursor = connection.execute(
                    """UPDATE sandbox_jobs SET status=?, exit_code=?, stdout=?, stderr=?,
                       error_code=?, error_message=?, finished_at=?, lease_expires_at=?,
                       updated_at=? WHERE id=? AND owner_token=?
                       AND status IN ('running', 'recovering')""",
                    (*values, timestamp, timestamp, job_id, owner_token),
                )
            else:
                cursor = connection.execute(
                    """UPDATE sandbox_jobs SET status=?, exit_code=?, stdout=?, stderr=?,
                       error_code=?, error_message=?, finished_at=?, owner_token=NULL,
                       lease_expires_at=NULL, container_name=NULL, current_attempt=NULL,
                       updated_at=? WHERE id=? AND owner_token=?
                       AND status IN ('running', 'recovering')""",
                    (*values, timestamp, job_id, owner_token),
                )
            if cursor.rowcount != 1:
                connection.rollback()
                raise _LeaseLost(f"job {job_id} ownership changed")
            connection.execute(
                """UPDATE sandbox_job_runs SET status=?, exit_code=?, error_code=?,
                   error_message=?, stdout=?, stderr=?, finished_at=?
                   WHERE job_id=? AND attempt=?""",
                (
                    status,
                    exit_code,
                    error_code,
                    error_message,
                    stdout.decode("utf-8", errors="replace"),
                    stderr.decode("utf-8", errors="replace"),
                    timestamp,
                    job_id,
                    attempt,
                ),
            )
            connection.commit()
        finally:
            self._close(connection)
        return self.get_job(job_id)  # type: ignore[return-value]

    def _heartbeat(self, job_id: str, attempt: int, owner_token: str) -> None:
        timestamp = _now()
        lease_expires_at = _after(self.lease_seconds)
        connection = self._connect()
        try:
            cursor = connection.execute(
                """UPDATE sandbox_jobs SET heartbeat_at=?, lease_expires_at=?, updated_at=?
                   WHERE id=? AND status='running' AND owner_token=?""",
                (timestamp, lease_expires_at, timestamp, job_id, owner_token),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise _LeaseLost(f"job {job_id} ownership changed")
            connection.execute(
                """UPDATE sandbox_job_runs SET heartbeat_at=?, lease_expires_at=?
                   WHERE job_id=? AND attempt=? AND owner_token=? AND status='running'""",
                (timestamp, lease_expires_at, job_id, attempt, owner_token),
            )
            connection.commit()
        finally:
            self._close(connection)

    def _claim_orphan(
        self, job_id: str, expected_owner: str | None, expired_before: str
    ) -> tuple[dict[str, Any], str] | None:
        recovery_token = secrets.token_urlsafe(24)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT * FROM sandbox_jobs WHERE id=?
                   AND status IN ('running', 'recovering', 'cleanup_failed')
                   AND owner_token IS ?
                   AND (lease_expires_at IS NULL OR lease_expires_at <= ?)""",
                (job_id, expected_owner, expired_before),
            ).fetchone()
            if row is None:
                connection.rollback()
                return None
            job = dict(row)
            run = connection.execute(
                """SELECT attempt, container_name, owner_token
                   FROM sandbox_job_runs WHERE job_id=?
                   ORDER BY attempt DESC LIMIT 1""",
                (job_id,),
            ).fetchone()
            if run is not None:
                if job.get("current_attempt") is None:
                    job["current_attempt"] = run["attempt"]
                if job.get("container_name") is None:
                    job["container_name"] = run["container_name"]
                job["container_owner_token"] = run["owner_token"]
            timestamp = _now()
            cursor = connection.execute(
                """UPDATE sandbox_jobs SET status='recovering', owner_token=?,
                   heartbeat_at=?, lease_expires_at=?, updated_at=?
                   WHERE id=? AND status IN ('running', 'recovering', 'cleanup_failed')
                   AND owner_token IS ?
                   AND (lease_expires_at IS NULL OR lease_expires_at <= ?)""",
                (
                    recovery_token,
                    timestamp,
                    _after(max(self.lease_seconds, 30)),
                    timestamp,
                    job_id,
                    expected_owner,
                    expired_before,
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return None
            if job.get("current_attempt") is not None:
                connection.execute(
                    """UPDATE sandbox_job_runs SET status='recovering'
                       WHERE job_id=? AND attempt=?
                       AND status IN ('running', 'recovering', 'cleanup_failed')""",
                    (job_id, job["current_attempt"]),
                )
            connection.commit()
            return job, recovery_token
        except Exception:
            connection.rollback()
            raise
        finally:
            self._close(connection)

    def recover_orphans(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Recover expired executions without touching jobs with a live lease."""
        if limit <= 0 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        expired_before = _now()
        connection = self._connect()
        try:
            rows = connection.execute(
                """SELECT id, owner_token FROM sandbox_jobs
                   WHERE status IN ('running', 'recovering', 'cleanup_failed')
                   AND (lease_expires_at IS NULL OR lease_expires_at <= ?)
                   ORDER BY started_at LIMIT ?""",
                (expired_before, limit),
            ).fetchall()
        finally:
            self._close(connection)

        recovered: list[dict[str, Any]] = []
        for candidate in rows:
            claimed = self._claim_orphan(
                candidate["id"], candidate["owner_token"], expired_before
            )
            if claimed is None:
                continue
            orphan, recovery_token = claimed
            attempt = orphan.get("current_attempt")
            container_name = orphan.get("container_name")
            original_owner = orphan.get(
                "container_owner_token", orphan.get("owner_token")
            )
            if (
                not isinstance(attempt, int)
                or not isinstance(container_name, str)
                or not isinstance(original_owner, str)
            ):
                cleanup = {
                    "ok": False,
                    "code": "missing_container_identity",
                    "message": "expired run has no complete container ownership record",
                }
                attempt = int(attempt or 0)
            else:
                cleanup = self._cleanup(
                    container_name, orphan["id"], attempt, original_owner
                )
            if cleanup["ok"]:
                result = self._finish(
                    orphan["id"],
                    attempt,
                    recovery_token,
                    status="failed",
                    error_code="owner_lease_expired",
                    error_message="execution owner lease expired; container absence confirmed",
                )
            else:
                result = self._finish(
                    orphan["id"],
                    attempt,
                    recovery_token,
                    status="cleanup_failed",
                    error_code=cleanup["code"],
                    error_message=(
                        "execution owner lease expired; container cleanup was not confirmed: "
                        f"{cleanup['message']}"
                    ),
                )
            recovered.append(result)
        return recovered

    def _inspect_container(self, container_name: str) -> dict[str, Any]:
        if not _CONTAINER_RE.fullmatch(container_name):
            return {
                "state": "error",
                "code": "invalid_container_identity",
                "message": "refusing an invalid container name",
            }
        try:
            result = subprocess.run(
                [
                    self.docker_executable,
                    "container",
                    "inspect",
                    "--format",
                    "{{json .Config.Labels}}",
                    container_name,
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5,
                check=False,
                text=True,
                env=self._docker_env(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"state": "error", "code": "inspect_failed", "message": str(exc)}
        if result.returncode != 0:
            message = result.stderr.strip() or "container inspection failed"
            lowered = message.lower()
            if "no such container" in lowered or "no such object" in lowered:
                return {"state": "absent", "code": "already_absent", "message": message}
            return {"state": "error", "code": "inspect_failed", "message": message}
        try:
            labels = json.loads(result.stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            return {"state": "error", "code": "invalid_labels", "message": str(exc)}
        if not isinstance(labels, dict):
            return {
                "state": "error",
                "code": "invalid_labels",
                "message": "container labels were not an object",
            }
        return {"state": "present", "labels": labels}

    def _cleanup(
        self,
        container_name: str,
        job_id: str,
        attempt: int,
        owner_token: str,
    ) -> dict[str, Any]:
        inspected = self._inspect_container(container_name)
        if inspected["state"] == "absent":
            return {
                "ok": True,
                "code": "already_absent",
                "message": inspected["message"],
            }
        if inspected["state"] != "present":
            return {
                "ok": False,
                "code": inspected["code"],
                "message": inspected["message"],
            }
        expected = {
            "kimi.sandbox.job": job_id,
            "kimi.sandbox.attempt": str(attempt),
            "kimi.sandbox.owner": owner_token,
        }
        actual = inspected["labels"]
        if any(actual.get(key) != value for key, value in expected.items()):
            return {
                "ok": False,
                "code": "container_identity_mismatch",
                "message": "refusing to delete a container with mismatched ownership labels",
            }
        try:
            removal = subprocess.run(
                [self.docker_executable, "rm", "-f", container_name],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5,
                check=False,
                text=True,
                env=self._docker_env(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"ok": False, "code": "container_remove_failed", "message": str(exc)}
        if removal.returncode != 0:
            message = removal.stderr.strip() or "docker rm -f failed"
            return {"ok": False, "code": "container_remove_failed", "message": message}
        verified = self._inspect_container(container_name)
        if verified["state"] == "absent":
            return {
                "ok": True,
                "code": "removed",
                "message": "container removal confirmed",
            }
        return {
            "ok": False,
            "code": "container_removal_unconfirmed",
            "message": verified.get("message", "container still exists after removal"),
        }

    @staticmethod
    def _terminate_process(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()

    def _collect(
        self,
        process: subprocess.Popen[bytes],
        source: bytes,
        heartbeat,
        deadline: float | None = None,
    ) -> tuple[bytes, bytes, str | None]:
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None
        deadline = deadline or (time.monotonic() + self.timeout_seconds)
        selector = selectors.DefaultSelector()
        for stream in (process.stdin, process.stdout, process.stderr):
            os.set_blocking(stream.fileno(), False)
        selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        output: dict[str, list[bytes]] = {"stdout": [], "stderr": []}
        total = 0
        source_offset = 0
        heartbeat_interval = min(1.0, self.lease_seconds / 3)
        next_heartbeat = time.monotonic() + heartbeat_interval
        reason: str | None = None

        def close_stdin() -> None:
            if not process.stdin.closed:
                try:
                    selector.unregister(process.stdin)
                except KeyError:
                    pass
                process.stdin.close()

        try:
            while selector.get_map():
                if time.monotonic() >= next_heartbeat:
                    heartbeat()
                    next_heartbeat = time.monotonic() + heartbeat_interval
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    reason = "timeout"
                    break
                until_heartbeat = max(0.0, next_heartbeat - time.monotonic())
                events = selector.select(min(remaining, 0.1, until_heartbeat))
                for key, _ in events:
                    if key.data == "stdin":
                        try:
                            written = os.write(
                                key.fileobj.fileno(),
                                source[source_offset : source_offset + 8192],
                            )
                        except BrokenPipeError:
                            close_stdin()
                            continue
                        except BlockingIOError:
                            continue
                        source_offset += written
                        if source_offset >= len(source):
                            close_stdin()
                        continue
                    try:
                        chunk = os.read(key.fileobj.fileno(), 8192)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    remaining_bytes = self.max_output_bytes - total
                    if len(chunk) > remaining_bytes:
                        output[key.data].append(chunk[: max(0, remaining_bytes)])
                        reason = "output_limit"
                        break
                    output[key.data].append(chunk)
                    total += len(chunk)
                if reason:
                    break
            if reason is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    reason = "timeout"
                else:
                    process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            reason = "timeout"
        finally:
            close_stdin()
            selector.close()
        return b"".join(output["stdout"]), b"".join(output["stderr"]), reason

    def run_job(self, job_id: str) -> dict[str, Any]:
        job = self.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        backend = self.check_backend()
        container_name = f"kimi-sandbox-{secrets.token_hex(12)}"
        attempt, owner_token = self._set_running(job_id, container_name)

        if not backend["available"]:
            return self._finish(
                job_id,
                attempt,
                owner_token,
                status="backend_unavailable",
                error_code=backend["code"],
                error_message=backend["message"],
            )

        process: subprocess.Popen[bytes] | None = None
        try:
            execution_deadline = time.monotonic() + self.timeout_seconds
            process = subprocess.Popen(
                self._command(container_name, job_id, attempt, owner_token),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self._docker_env(),
                start_new_session=True,
            )
            stdout, stderr, reason = self._collect(
                process,
                job["source"].encode(),
                lambda: self._heartbeat(job_id, attempt, owner_token),
                execution_deadline,
            )
            if reason is not None:
                self._terminate_process(process)
                cleanup = self._cleanup(container_name, job_id, attempt, owner_token)
                if not cleanup["ok"]:
                    return self._finish(
                        job_id,
                        attempt,
                        owner_token,
                        status="cleanup_failed",
                        stdout=stdout,
                        stderr=stderr,
                        error_code=cleanup["code"],
                        error_message=(
                            f"execution stopped because of {reason}; "
                            f"container cleanup was not confirmed: {cleanup['message']}"
                        ),
                    )
                if reason == "timeout":
                    return self._finish(
                        job_id,
                        attempt,
                        owner_token,
                        status="timed_out",
                        stdout=stdout,
                        stderr=stderr,
                        error_code="timeout",
                        error_message=f"execution exceeded {self.timeout_seconds:g} seconds",
                    )
                return self._finish(
                    job_id,
                    attempt,
                    owner_token,
                    status="output_limited",
                    stdout=stdout,
                    stderr=stderr,
                    error_code="output_limit",
                    error_message=f"output exceeded {self.max_output_bytes} bytes",
                )
            status = "succeeded" if process.returncode == 0 else "failed"
            return self._finish(
                job_id,
                attempt,
                owner_token,
                status=status,
                exit_code=process.returncode,
                stdout=stdout,
                stderr=stderr,
                error_code=None if status == "succeeded" else "nonzero_exit",
                error_message=None
                if status == "succeeded"
                else "container exited unsuccessfully",
            )
        except _LeaseLost:
            if process is not None:
                self._terminate_process(process)
            current = self.get_job(job_id)
            if current is None:
                raise KeyError(job_id)
            return current
        except OSError as exc:
            if process is not None:
                self._terminate_process(process)
                cleanup = self._cleanup(container_name, job_id, attempt, owner_token)
                return self._finish(
                    job_id,
                    attempt,
                    owner_token,
                    status="failed" if cleanup["ok"] else "cleanup_failed",
                    error_code="execution_error" if cleanup["ok"] else cleanup["code"],
                    error_message=(
                        str(exc)
                        if cleanup["ok"]
                        else f"{exc}; container cleanup was not confirmed: {cleanup['message']}"
                    ),
                )
            return self._finish(
                job_id,
                attempt,
                owner_token,
                status="backend_unavailable",
                error_code="docker_start_failed",
                error_message=str(exc),
            )
        except BrokenPipeError as exc:
            if process is not None:
                self._terminate_process(process)
            cleanup = self._cleanup(container_name, job_id, attempt, owner_token)
            return self._finish(
                job_id,
                attempt,
                owner_token,
                status="failed" if cleanup["ok"] else "cleanup_failed",
                error_code="broken_pipe" if cleanup["ok"] else cleanup["code"],
                error_message=(
                    str(exc)
                    if cleanup["ok"]
                    else f"{exc}; container cleanup was not confirmed: {cleanup['message']}"
                ),
            )
        except Exception as exc:
            if process is not None:
                self._terminate_process(process)
            cleanup = self._cleanup(container_name, job_id, attempt, owner_token)
            return self._finish(
                job_id,
                attempt,
                owner_token,
                status="failed" if cleanup["ok"] else "cleanup_failed",
                error_code="execution_error" if cleanup["ok"] else cleanup["code"],
                error_message=(
                    str(exc)
                    if cleanup["ok"]
                    else f"{exc}; container cleanup was not confirmed: {cleanup['message']}"
                ),
            )
