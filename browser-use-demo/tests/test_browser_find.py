"""Tests for AI-assisted browser element search."""

import asyncio
import os
from unittest.mock import AsyncMock, Mock, patch

import pytest
from anthropic.types import TextBlock

from browser_use_demo.loop import APIProvider, sampling_loop
from browser_use_demo.message_handler import ResponseProcessor
from browser_use_demo.tools import BrowserTool, ToolCollection, ToolError


def _ready_browser(dom: str) -> BrowserTool:
    browser = BrowserTool()
    browser._page = Mock()
    browser._execute_js_from_file = AsyncMock(
        return_value={"pageContent": dom}
    )
    return browser


class TestBrowserFindConfiguration:
    @patch("browser_use_demo.tools.browser.AsyncAnthropic")
    @patch("browser_use_demo.loop.Anthropic")
    def test_sampling_loop_injects_active_ui_configuration(
        self,
        mock_chat_anthropic,
        mock_find_anthropic,
    ):
        async def run_test():
            chat_client = Mock()
            chat_client.messages.create.return_value = Mock(
                content=[TextBlock(type="text", text="Done")],
                stop_reason="end_turn",
            )
            mock_chat_anthropic.return_value = chat_client

            find_client = Mock()
            find_client.messages.create = AsyncMock(
                return_value=Mock(
                    content=[
                        TextBlock(
                            type="text",
                            text=(
                                "FOUND: 1\nSHOWING: 1\n---\n"
                                "ref_7 | button | Submit | submit | requested button"
                            ),
                        )
                    ],
                    stop_reason="end_turn",
                )
            )
            mock_find_anthropic.return_value = find_client
            browser = _ready_browser('- button "Submit" [ref=ref_7]')

            with patch.dict(
                os.environ,
                {
                    "KIMI_API_KEY": "wrong-environment-key",
                    "ANTHROPIC_API_KEY": "another-wrong-key",
                    "ANTHROPIC_BASE_URL": "https://wrong.example/",
                },
            ):
                await sampling_loop(
                    model="ui-model",
                    provider=APIProvider.KIMI,
                    system_prompt_suffix="",
                    messages=[{"role": "user", "content": "Find submit"}],
                    output_callback=lambda _: None,
                    tool_output_callback=lambda *_: None,
                    api_response_callback=lambda *_: None,
                    api_key="ui-key",
                    base_url="https://ui.example/",
                    browser_tool=browser,
                )
                result = await browser._find("Submit")

            mock_find_anthropic.assert_called_once_with(
                api_key="ui-key",
                base_url="https://ui.example/",
            )
            find_client.messages.create.assert_awaited_once()
            assert find_client.messages.create.await_args.kwargs["model"] == "ui-model"
            assert "ref_7" in result.output

        asyncio.run(run_test())

    @patch("browser_use_demo.tools.browser.AsyncAnthropic")
    def test_provider_switch_replaces_find_client_configuration(
        self,
        mock_anthropic,
    ):
        kimi_client = Mock()
        anthropic_client = Mock()
        mock_anthropic.side_effect = [kimi_client, anthropic_client]
        browser = BrowserTool()

        browser.configure_find(
            provider=APIProvider.KIMI,
            api_key="kimi-key",
            base_url="https://api.kimi.example/",
            model="kimi-model",
        )
        browser.configure_find(
            provider=APIProvider.ANTHROPIC,
            api_key="anthropic-key",
            base_url=None,
            model="anthropic-model",
        )

        assert browser._find_client is anthropic_client
        assert browser._find_provider == "anthropic"
        assert browser._find_model == "anthropic-model"
        assert mock_anthropic.call_args_list[1].kwargs == {
            "api_key": "anthropic-key"
        }


class TestBrowserFindFailures:
    @pytest.mark.parametrize(
        "failure",
        [RuntimeError("401 unauthorized"), RuntimeError("429 rate limited")],
    )
    def test_configured_request_errors_are_visible(self, failure):
        async def run_test():
            browser = _ready_browser('- button "Submit" [ref=ref_7]')
            browser._find_provider = "kimi"
            browser._find_model = "kimi-model"
            browser._find_client = Mock()
            browser._find_client.messages.create = AsyncMock(side_effect=failure)

            with pytest.raises(ToolError) as exc_info:
                await browser._find("Submit")

            assert str(failure) in exc_info.value.message
            assert str(failure) in str(exc_info.value)

        asyncio.run(run_test())

    def test_parse_failure_is_visible(self):
        async def run_test():
            browser = _ready_browser('- button "Submit" [ref=ref_7]')
            browser._find_provider = "kimi"
            browser._find_model = "kimi-model"
            browser._find_client = Mock()
            browser._find_client.messages.create = AsyncMock(
                return_value=Mock(
                    content=[TextBlock(type="text", text="I found a button")],
                    stop_reason="end_turn",
                )
            )

            with pytest.raises(ToolError) as exc_info:
                await browser._find("Submit")

            assert "did not include FOUND count" in exc_info.value.message

        asyncio.run(run_test())

    def test_request_error_reaches_tool_result(self):
        async def run_test():
            browser = _ready_browser('- button "Submit" [ref=ref_7]')
            browser._find_provider = "kimi"
            browser._find_model = "kimi-model"
            browser._find_client = Mock()
            browser._find_client.messages.create = AsyncMock(
                side_effect=RuntimeError("401 unauthorized")
            )

            results = await ResponseProcessor().execute_tools(
                [
                    {
                        "type": "tool_use",
                        "id": "find_1",
                        "name": "browser",
                        "input": {"action": "find", "text": "Submit"},
                    }
                ],
                ToolCollection(browser),
            )

            assert results[0]["is_error"] is True
            assert "401 unauthorized" in results[0]["content"][0]["text"]

        asyncio.run(run_test())

    def test_unconfigured_local_fallback_returns_actionable_refs_and_reason(self):
        async def run_test():
            browser = _ready_browser(
                '- button "Submit order" [ref=ref_7] type="submit"\n'
                '- link "Cancel" [ref=ref_8]'
            )

            result = await browser._find("submit")

            assert "AI element search is not configured" in result.output
            assert "ref_7" in result.output
            assert "ref_8" not in result.output

        asyncio.run(run_test())
