"""Cross-feature tests: persisted jobs and evidence reach real runtime boundaries."""

import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from kimi import _runtime, feature_command, parser, worker
from kimi_local.http_api import make_server
from kimi_local.knowledge import KnowledgeStore
from kimi_local.provider import KimiProvider
from kimi_local.runtime import LocalRuntime
from kimi_local.sandbox import SandboxExecutor
from kimi_local.scheduler import Scheduler
from kimi_local.store import SessionStore


class EvidenceProvider:
    calls = []

    def complete(self, system, messages):
        self.calls.append((system, messages))
        return "Answer from local evidence"


class FeatureTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "state.db"
        self.documents = Path(self.directory.name) / "documents"
        self.documents.mkdir()
        EvidenceProvider.calls = []
        self.runtime = LocalRuntime(
            SessionStore(self.path),
            EvidenceProvider,
            knowledge=KnowledgeStore(self.path),
            sandbox=SandboxExecutor(self.path),
            document_root=self.documents,
        )
        self.runtime.scheduler = Scheduler(self.path, self.runtime.run_scheduled)

    def tearDown(self):
        self.directory.cleanup()

    def test_retrieval_is_injected_and_provenance_persists(self):
        self.runtime.knowledge.ingest_text(
            "Refunds are allowed within fourteen days.", "policy.md"
        )
        self.runtime.knowledge.ingest_text(
            "Secret collection content.", "secret.md", "other"
        )
        session = self.runtime.create_session("knowledge-wiki")
        reply = self.runtime.chat(session["id"], "What is the refund policy?")
        system = EvidenceProvider.calls[-1][0]
        self.assertIn("fourteen days", system)
        self.assertNotIn("Secret collection content", system)
        self.assertIn("untrusted document data", system)
        self.assertTrue(reply["metadata"]["sources"])
        source = reply["metadata"]["sources"][0]
        self.assertIn(source["citation_id"], system)
        reopened = SessionStore(self.path)
        self.assertEqual(
            reopened.events(session["id"])[-1]["metadata"]["sources"],
            reply["metadata"]["sources"],
        )

    def test_path_ingestion_cannot_escape_document_root(self):
        (self.documents / "help.md").write_text("Account help", encoding="utf-8")
        outcome = self.runtime.ingest_knowledge({"path": "help.md"})
        self.assertEqual(outcome["created"], 1)
        with self.assertRaisesRegex(ValueError, "inside"):
            self.runtime.ingest_knowledge({"path": "../state.db"})
        with self.assertRaises(ValueError):
            self.runtime.ingest_knowledge({"path": "help.md", "text": "ambiguous"})

    def test_scheduled_run_creates_session_and_uses_knowledge(self):
        self.runtime.knowledge.ingest_text(
            "Release deployment succeeded.", "release.md"
        )
        job = self.runtime.scheduler.add_job(
            "daily-brief",
            "Summarize the release deployment",
            run_at=datetime.now(timezone.utc),
            knowledge_collection="default",
        )
        runs = self.runtime.scheduler.run_due()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "succeeded")
        session_id = runs[0]["result"]["session_id"]
        self.assertEqual(
            self.runtime.store.get_session(session_id)["knowledge_collection"],
            "default",
        )
        events = self.runtime.store.events(session_id)
        self.assertEqual(events[0]["metadata"]["job_id"], job["id"])
        self.assertTrue(events[-1]["metadata"]["sources"])
        self.assertEqual(self.runtime.scheduler.run_due(), [])

    def test_legacy_session_database_is_migrated_without_data_loss(self):
        legacy = Path(self.directory.name) / "legacy.db"
        with closing(sqlite3.connect(legacy)) as connection, connection:
            connection.execute(
                "CREATE TABLE sessions(id TEXT PRIMARY KEY, profile TEXT, title TEXT, status TEXT, created_at TEXT, updated_at TEXT)"
            )
            connection.execute(
                "INSERT INTO sessions VALUES ('old', 'slack', 'Existing', 'idle', 'now', 'now')"
            )
        store = SessionStore(legacy)
        self.assertIsNone(store.get_session("old")["knowledge_collection"])
        self.assertEqual(store.get_session("old")["title"], "Existing")
        self.assertEqual(
            store.create_session("knowledge-wiki", knowledge_collection="default")[
                "knowledge_collection"
            ],
            "default",
        )

    def test_cli_features_use_same_persistent_services(self):
        document = self.documents / "guide.txt"
        document.write_text("Kimi supports local document retrieval.", encoding="utf-8")
        args = parser().parse_args(
            ["--db", str(self.path), "knowledge", "ingest", str(document)]
        )
        self.assertEqual(feature_command(self.runtime, args)["created"], 1)
        reopened = _runtime(str(self.path))
        args = parser().parse_args(["knowledge", "search", "document retrieval"])
        self.assertTrue(feature_command(reopened, args))

    def test_failed_scheduled_run_links_to_its_error_session(self):
        class BrokenProvider:
            def complete(self, *_):
                raise RuntimeError("Unavailable")

        self.runtime.provider_factory = BrokenProvider
        self.runtime.scheduler.add_job(
            "daily-brief", "Summarize", run_at=datetime.now(timezone.utc)
        )
        run = self.runtime.scheduler.run_due()[0]
        self.assertEqual(run["status"], "failed")
        session_id = run["error"]["session_id"]
        self.assertEqual(self.runtime.store.get_session(session_id)["status"], "error")

    def test_worker_executes_due_task_without_manual_tick(self):
        ran = threading.Event()

        class WorkerProvider:
            def complete(self, *_):
                ran.set()
                return "Background result"

        self.runtime.provider_factory = WorkerProvider
        job = self.runtime.scheduler.add_job(
            "daily-brief", "Summarize", run_at=datetime.now(timezone.utc)
        )
        stopped = threading.Event()
        thread = threading.Thread(
            target=worker, args=(self.runtime, stopped, 0.1), daemon=True
        )
        thread.start()
        try:
            self.assertTrue(ran.wait(5), "The worker did not execute a due task")
        finally:
            stopped.set()
            thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(
            self.runtime.scheduler.list_runs(job["id"])[0]["status"], "succeeded"
        )


class ProviderToolTests(unittest.TestCase):
    def test_signed_thinking_and_tool_results_are_replayed(self):
        from anthropic.types import Message, TextBlock, ThinkingBlock, ToolUseBlock

        provider = KimiProvider.__new__(KimiProvider)
        provider.model = "kimi-for-coding"
        provider.max_tokens = 4096
        provider.client = SimpleNamespace(messages=MagicMock())
        first = Message.model_construct(
            content=[
                ThinkingBlock(
                    type="thinking", thinking="Use the sandbox", signature="signature"
                ),
                ToolUseBlock(
                    type="tool_use",
                    id="tool1",
                    name="run_python",
                    input={"code": "print(2)"},
                ),
            ],
            stop_reason="tool_use",
        )
        last = Message.model_construct(
            content=[TextBlock(type="text", text="2")], stop_reason="end_turn"
        )
        provider.client.messages.create.side_effect = [first, last]
        executed = []
        reply = provider.complete_with_tools(
            "system",
            [{"role": "user", "content": "calculate"}],
            tools=[{"name": "run_python"}],
            execute=lambda name, args: (
                executed.append((name, args)) or {"status": "succeeded", "stdout": "2"}
            ),
            max_turns=1,
        )
        self.assertEqual(reply, "2")
        self.assertEqual(executed[0][0], "run_python")
        history = provider.client.messages.create.call_args.kwargs["messages"]
        self.assertEqual(history[1]["content"][0]["signature"], "signature")
        self.assertEqual(history[2]["content"][0]["tool_use_id"], "tool1")
        self.assertEqual(json.loads(history[2]["content"][0]["content"])["stdout"], "2")
        self.assertEqual(
            provider.client.messages.create.call_args.kwargs["tool_choice"],
            {"type": "none"},
        )


class FeatureHttpTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        path = Path(self.directory.name) / "api.db"
        self.runtime = LocalRuntime(
            SessionStore(path),
            EvidenceProvider,
            knowledge=KnowledgeStore(path),
            sandbox=SandboxExecutor(path),
        )
        self.runtime.scheduler = Scheduler(path, self.runtime.run_scheduled)
        self.server = make_server(self.runtime, "127.0.0.1", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.directory.cleanup()

    def request(self, path, payload=None, method=None):
        request = Request(
            self.url + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        with urlopen(request) as response:
            return json.load(response)

    def test_ingest_search_chat_and_document_delete(self):
        result = self.request(
            "/v1/knowledge/documents",
            {"source": "guide.md", "text": "Subscriptions renew every month."},
        )
        document_id = result["document"]["id"]
        self.assertTrue(self.request("/v1/knowledge/search?q=subscriptions")["results"])
        session = self.request("/v1/sessions", {"profile": "knowledge-wiki"})["session"]
        reply = self.request(
            f"/v1/sessions/{session['id']}/messages", {"content": "subscriptions"}
        )
        self.assertTrue(reply["event"]["metadata"]["sources"])
        self.assertTrue(
            self.request(f"/v1/knowledge/documents/{document_id}", method="DELETE")[
                "deleted"
            ]
        )

    def test_schedules_and_sandbox_routes_persist_results(self):
        job = self.request(
            "/v1/schedules",
            {
                "profile": "daily-brief",
                "prompt": "Summarize",
                "run_at": "2020-01-01T00:00:00Z",
            },
        )["job"]
        self.assertEqual(
            self.request("/v1/schedules/tick", {})["runs"][0]["status"], "succeeded"
        )
        self.assertTrue(self.request(f"/v1/schedules/runs?job_id={job['id']}")["runs"])
        with self.assertRaises(HTTPError) as caught:
            self.request(f"/v1/schedules/{job['id']}/enable", {})
        self.assertEqual(caught.exception.code, 400)
        sandbox_job = self.request("/v1/sandbox/jobs", {"code": "print(2)"})["job"]
        self.assertEqual(sandbox_job["status"], "queued")
        self.assertEqual(
            self.request(f"/v1/sandbox/jobs/{sandbox_job['id']}")["job"]["id"],
            sandbox_job["id"],
        )
        self.assertTrue(self.request("/v1/capabilities")["knowledge"])

    def test_invalid_job_parameters_fail_before_creation(self):
        with self.assertRaises(HTTPError) as caught:
            self.request("/v1/schedules", {"prompt": "hi", "run_at": "not-a-date"})
        self.assertEqual(caught.exception.code, 400)
        self.assertEqual(self.request("/v1/schedules")["jobs"], [])

    def test_remote_binding_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "loopback"):
            make_server(self.runtime, "0.0.0.0", 0)

    def test_nonstandard_json_numbers_are_rejected(self):
        with self.assertRaises(HTTPError) as caught:
            self.request(
                "/v1/schedules", {"prompt": "hi", "interval_seconds": float("nan")}
            )
        self.assertEqual(caught.exception.code, 400)
        self.assertEqual(self.request("/v1/schedules")["jobs"], [])


if __name__ == "__main__":
    unittest.main()
