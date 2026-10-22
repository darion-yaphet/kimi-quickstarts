"""Tests for the local Kimi session runtime and HTTP API."""

import json
import http.client
import os
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import MagicMock, patch

from kimi_local.http_api import make_server
from kimi_local.profiles import PROFILES
from kimi_local.provider import KimiProvider
from kimi_local.runtime import LocalRuntime
from kimi_local.store import SessionStore


class FakeProvider:
    calls = []

    def complete(self, system, messages):
        self.calls.append((system, list(messages)))
        return f"reply:{messages[-1]['content']}"


class BrokenProvider:
    def complete(self, system, messages):
        raise RuntimeError("provider unavailable")


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        FakeProvider.calls = []
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "sessions.db"
        self.store = SessionStore(self.database)
        self.runtime = LocalRuntime(self.store, FakeProvider)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_all_documented_profiles_exist(self):
        self.assertEqual(
            set(PROFILES),
            {
                "assistant-ui",
                "chat-sdk",
                "copilot-kit-ag-ui",
                "daily-brief",
                "knowledge-wiki",
                "linear",
                "mcp-server-typescript",
                "roadtrip-planner",
                "self-hosted-sandboxes",
                "sentry",
                "slack",
            },
        )
        for profile in PROFILES.values():
            self.assertTrue(profile.remaining_integrations)
            self.assertFalse(profile.public_dict()["hosted_capabilities"]["sessions"])

    def test_conversation_and_events_persist(self):
        session = self.runtime.create_session("slack", "Draft a reply")
        event = self.runtime.chat(session["id"], "hello")
        self.assertEqual(event["content"], "reply:hello")
        self.assertIn("Never claim an external action ran", FakeProvider.calls[0][0])

        reopened = SessionStore(self.database)
        self.assertEqual(reopened.get_session(session["id"])["status"], "idle")
        self.assertEqual(
            [(item["role"], item["content"]) for item in reopened.events(session["id"])],
            [("user", "hello"), ("assistant", "reply:hello")],
        )

    def test_provider_failure_is_recorded(self):
        runtime = LocalRuntime(self.store, BrokenProvider)
        session = runtime.create_session("sentry")
        with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
            runtime.chat(session["id"], "triage this")
        self.assertEqual(self.store.get_session(session["id"])["status"], "error")
        self.assertEqual(self.store.events(session["id"])[-1]["type"], "error")

    def test_invalid_profile_type_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "must be a string"):
            self.runtime.create_session(["slack"])


class ProviderTests(unittest.TestCase):
    def provider_with_response(self, response, environment=None):
        client = SimpleNamespace(messages=MagicMock())
        client.messages.create.return_value = response
        anthropic = types.ModuleType("anthropic")
        anthropic.Anthropic = MagicMock(return_value=client)
        values = {"ANTHROPIC_API_KEY": "test-key", **(environment or {})}
        with patch.dict(os.environ, values, clear=True):
            with patch.dict(sys.modules, {"anthropic": anthropic}):
                provider = KimiProvider()
        return provider, anthropic.Anthropic, client

    def test_provider_configures_endpoint_model_limit_and_timeout(self):
        response = SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text="done")],
        )
        provider, constructor, client = self.provider_with_response(
            response,
            {
                "ANTHROPIC_BASE_URL": "https://api.kimi.ai/coding/",
                "ANTHROPIC_MODEL": "k3-256k",
                "ANTHROPIC_MAX_TOKENS": "2048",
                "ANTHROPIC_TIMEOUT_SECONDS": "45",
            },
        )
        constructor.assert_called_once_with(
            api_key="test-key",
            base_url="https://api.kimi.ai/coding/",
            timeout=45.0,
        )
        self.assertEqual(provider.complete("system", [{"role": "user", "content": "hi"}]), "done")
        client.messages.create.assert_called_once_with(
            model="k3-256k",
            max_tokens=2048,
            system="system",
            messages=[{"role": "user", "content": "hi"}],
        )

    def test_truncated_response_is_an_error(self):
        response = SimpleNamespace(
            stop_reason="max_tokens",
            content=[SimpleNamespace(type="text", text="partial")],
        )
        provider, _, _ = self.provider_with_response(response)
        with self.assertRaisesRegex(RuntimeError, "truncated"):
            provider.complete("system", [{"role": "user", "content": "hi"}])


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        store = SessionStore(Path(self.tempdir.name) / "http.db")
        self.runtime = LocalRuntime(store, FakeProvider)
        self.server = make_server(self.runtime, "127.0.0.1", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tempdir.cleanup()

    def request(self, method, path, payload=None, headers=None):
        data = None if payload is None else json.dumps(payload).encode()
        request = Request(
            self.base + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        with urlopen(request) as response:
            return response.status, json.load(response)

    def test_http_session_chat_and_replay(self):
        status, created = self.request(
            "POST", "/v1/sessions", {"profile": "roadtrip-planner"}
        )
        self.assertEqual(status, 201)
        session_id = created["session"]["id"]

        status, reply = self.request(
            "POST",
            f"/v1/sessions/{session_id}/messages",
            {"content": "Plan two days"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(reply["event"]["content"], "reply:Plan two days")

        _, replay = self.request("GET", f"/v1/sessions/{session_id}/events")
        self.assertEqual(len(replay["events"]), 2)

    def test_root_serves_dependency_free_safe_ui(self):
        with urlopen(self.base + "/") as response:
            page = response.read().decode()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers.get_content_type(), "text/html")
            self.assertIn("Legacy UIs and external connectors remain pending", page)
            self.assertIn("textContent", page)
            self.assertNotIn("innerHTML", page)
            self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])

    def test_unknown_profile_is_a_bad_request(self):
        with self.assertRaises(HTTPError) as caught:
            self.request("POST", "/v1/sessions", {"profile": "unknown"})
        self.assertEqual(caught.exception.code, 400)

    def test_non_string_profiles_are_bad_requests(self):
        for profile in (["slack"], {"name": "slack"}):
            with self.subTest(profile=profile):
                with self.assertRaises(HTTPError) as caught:
                    self.request("POST", "/v1/sessions", {"profile": profile})
                self.assertEqual(caught.exception.code, 400)

    def test_post_requires_json_content_type(self):
        request = Request(
            self.base + "/v1/sessions",
            data=b'{"profile":"slack"}',
            method="POST",
            headers={"Content-Type": "text/plain"},
        )
        with self.assertRaises(HTTPError) as caught:
            urlopen(request)
        self.assertEqual(caught.exception.code, 415)

    def test_foreign_origin_is_rejected(self):
        with self.assertRaises(HTTPError) as caught:
            self.request(
                "POST",
                "/v1/sessions",
                {"profile": "slack"},
                {"Origin": "https://attacker.example"},
            )
        self.assertEqual(caught.exception.code, 403)

    def test_non_loopback_host_is_rejected(self):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=2
        )
        connection.putrequest("GET", "/health", skip_host=True)
        connection.putheader("Host", "attacker.example")
        connection.endheaders()
        response = connection.getresponse()
        response.read()
        connection.close()
        self.assertEqual(response.status, 403)


if __name__ == "__main__":
    unittest.main()
