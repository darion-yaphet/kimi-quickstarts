from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from anthropic.tools import ToolError
from anthropic.tools.browser import (
    BetaAbstractBrowserToolset20260801,
    BetaBrowserNavigateResult,
    BetaBrowserState,
    BetaToolsetCallContext,
)
from anthropic.types.beta import BetaBrowserNavigateInput, BetaBrowserReadPageInput

from kimi_adapter import MAX_OUTPUT_TOKENS, dispatch, portable_content, run_loop


class DummyBrowser(BetaAbstractBrowserToolset20260801):
    def __init__(self) -> None:
        self.navigate_calls = 0

        def policy(_context: object, url: str) -> None:
            if not url.startswith("https://allowed.example/"):
                raise ToolError(f"blocked: {url}")

        super().__init__(url_policy=policy)

    def navigate(
        self, _context: BetaToolsetCallContext, input: BetaBrowserNavigateInput
    ) -> BetaBrowserNavigateResult:
        self.navigate_calls += 1
        return BetaBrowserNavigateResult(url=input.url, title="Allowed")

    def read_page(self, _context: BetaToolsetCallContext, _input: BetaBrowserReadPageInput) -> str:
        return '- heading "Example" [ref_1]'

    def _browser_state(self, _context: BetaToolsetCallContext) -> BetaBrowserState:
        return BetaBrowserState(
            tabs=[
                {
                    "tab_id": "tab_1",
                    "url": "https://allowed.example/",
                    "title": "Allowed",
                    "active": True,
                }
            ]
        )


class TruncatedMessages:
    def __init__(self) -> None:
        self.params: dict[str, Any] = {}

    def create(self, **params: Any) -> SimpleNamespace:
        self.params = params
        return SimpleNamespace(content=[], stop_reason="max_tokens")


class KimiAdapterTests(unittest.TestCase):
    def test_sdk_policy_runs_and_browser_state_becomes_plain_text(self) -> None:
        browser = DummyBrowser()

        blocked = dispatch(browser, "blocked", "navigate", {"url": "https://blocked.example/"})
        self.assertTrue(blocked.get("is_error"))
        self.assertEqual(browser.navigate_calls, 0)

        allowed = dispatch(browser, "allowed", "navigate", {"url": "https://allowed.example/page"})
        self.assertFalse(allowed.get("is_error", False))
        self.assertEqual(browser.navigate_calls, 1)
        self.assertTrue(
            any(block.get("type") == "browser_state" for block in allowed.get("content", []))
        )

        content = portable_content(allowed)
        self.assertIsInstance(content, list)
        self.assertFalse(any(block.get("type") == "browser_state" for block in content))
        self.assertTrue(any(block.get("text", "").startswith("Browser state: ") for block in content))

    def test_truncated_response_is_not_treated_as_success(self) -> None:
        messages = TruncatedMessages()
        client = SimpleNamespace(messages=messages)

        with self.assertRaisesRegex(RuntimeError, "truncated"):
            run_loop(client, DummyBrowser(), "test", model="kimi-for-coding")

        self.assertEqual(messages.params["max_tokens"], MAX_OUTPUT_TOKENS)
        self.assertEqual(MAX_OUTPUT_TOKENS, 8192)


if __name__ == "__main__":
    unittest.main()
