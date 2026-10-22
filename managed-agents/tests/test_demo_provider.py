"""Tests for the deterministic offline demo provider."""

import json
import unittest

from kimi_local.demo_provider import DEMO_PREFIX, DemoProvider
from kimi_local.profiles import PROFILES


class DemoProviderTests(unittest.TestCase):
    def setUp(self):
        self.provider = DemoProvider()

    def test_all_profiles_return_deterministic_marked_demo_output(self):
        for profile in PROFILES.values():
            with self.subTest(profile=profile.name):
                messages = [{"role": "user", "content": "Please help"}]
                first = self.provider.complete(profile.instructions, messages)
                second = self.provider.complete(profile.instructions, messages)
                self.assertEqual(first, second)
                self.assertTrue(first.startswith(DEMO_PREFIX))
                self.assertIn("Please help", first)
                self.assertIn("did not contact a model", first)

    def test_only_cites_evidence_supplied_in_retrieval_context(self):
        payload = {
            "collection": "default",
            "sources": [
                {
                    "citation_id": "doc:0001",
                    "content": "Refunds are available for fourteen days.",
                    "source": "policy.md",
                }
            ],
        }
        system = (
            PROFILES["knowledge-wiki"].instructions
            + "\nRetrieved context:\n"
            + json.dumps(payload)
            + "\nNo code-execution tool is available."
        )

        reply = self.provider.complete(
            system,
            [{"role": "user", "content": "What is the refund policy? [invented]"}],
        )

        self.assertIn("Refunds are available for fourteen days. [doc:0001]", reply)
        self.assertNotIn("[policy.md]", reply)
        self.assertNotIn("[invented]", reply)

    def test_empty_retrieval_context_reports_missing_evidence(self):
        system = (
            PROFILES["knowledge-wiki"].instructions
            + "\n"
            + json.dumps({"collection": "default", "sources": []})
        )
        reply = self.provider.complete(
            system, [{"role": "user", "content": "Unknown fact"}]
        )
        self.assertIn("No relevant local evidence was supplied", reply)
        self.assertNotIn("[", reply)

    def test_tool_completion_never_executes_callback(self):
        calls = []
        reply = self.provider.complete_with_tools(
            PROFILES["self-hosted-sandboxes"].instructions,
            [{"role": "user", "content": "Run print(1)"}],
            tools=[{"name": "run_python"}],
            execute=lambda name, arguments: calls.append((name, arguments)),
        )
        self.assertEqual(calls, [])
        self.assertIn("No tools were executed", reply)
        self.assertIn(DEMO_PREFIX, reply)


if __name__ == "__main__":
    unittest.main()
