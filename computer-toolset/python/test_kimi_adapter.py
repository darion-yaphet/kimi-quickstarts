from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from kimi_adapter import MAX_OUTPUT_TOKENS, run_loop


class TruncatedMessages:
    def __init__(self) -> None:
        self.params: dict[str, Any] = {}

    def create(self, **params: Any) -> SimpleNamespace:
        self.params = params
        return SimpleNamespace(content=[], stop_reason="max_tokens")


class KimiAdapterTests(unittest.TestCase):
    def test_truncated_response_is_not_treated_as_success(self) -> None:
        messages = TruncatedMessages()
        client = SimpleNamespace(messages=messages)
        computer = SimpleNamespace()

        with self.assertRaisesRegex(RuntimeError, "truncated"):
            run_loop(client, computer, "test", model="kimi-for-coding")

        self.assertEqual(messages.params["max_tokens"], MAX_OUTPUT_TOKENS)
        self.assertEqual(MAX_OUTPUT_TOKENS, 8192)


if __name__ == "__main__":
    unittest.main()
