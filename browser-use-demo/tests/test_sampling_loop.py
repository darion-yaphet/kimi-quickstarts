"""
Comprehensive tests for the sampling loop and message handling.
"""

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock, Mock, patch

import pytest
from anthropic.types import TextBlock, ToolUseBlock
from browser_use_demo.loop import (
    APIProvider,
    _maybe_filter_to_n_most_recent_images,
    sampling_loop,
)
from browser_use_demo.message_handler import (
    MessageBuilder,
    ResponseProcessor,
)
from browser_use_demo.tools import ToolResult


def _screenshot_history(count: int):
    messages = []
    for index in range(count):
        tool_id = f"tool_{index}"
        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": tool_id,
                            "name": "browser",
                            "input": {"action": "screenshot"},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_id,
                            "content": [
                                {"type": "text", "text": f"screenshot {index}"},
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "image/png",
                                        "data": f"image-{index}",
                                    },
                                },
                            ],
                        }
                    ],
                },
            ]
        )
    return messages


def _nested_images(messages):
    return [
        nested
        for message in messages
        for block in message.get("content", [])
        if isinstance(block, dict) and block.get("type") == "tool_result"
        for nested in block.get("content", [])
        if isinstance(nested, dict) and nested.get("type") == "image"
    ]


class TestRecentImageFiltering:
    def test_filters_nested_tool_result_images_without_breaking_tool_pairs(self):
        messages = _screenshot_history(13)

        _maybe_filter_to_n_most_recent_images(messages, images_to_keep=3)

        assert [image["source"]["data"] for image in _nested_images(messages)] == [
            "image-10",
            "image-11",
            "image-12",
        ]
        tool_use_ids = [
            block["id"]
            for message in messages
            if message["role"] == "assistant"
            for block in message["content"]
            if block["type"] == "tool_use"
        ]
        tool_result_ids = [
            block["tool_use_id"]
            for message in messages
            if message["role"] == "user"
            for block in message["content"]
            if block["type"] == "tool_result"
        ]
        assert tool_result_ids == tool_use_ids
        assert all(
            any(nested.get("type") == "text" for nested in block["content"])
            for message in messages
            if message["role"] == "user"
            for block in message["content"]
        )

    @pytest.mark.parametrize("screenshot_count", [4, 13])
    @patch("browser_use_demo.loop.Anthropic")
    def test_sampling_loop_applies_requested_image_limit(
        self,
        mock_anthropic,
        screenshot_count,
    ):
        async def run_test():
            mock_client = Mock()
            mock_anthropic.return_value = mock_client
            captured_messages = []

            def create(**kwargs):
                captured_messages.append(deepcopy(kwargs["messages"]))
                return Mock(
                    content=[Mock(type="text", text="Done")],
                    stop_reason="end_turn",
                )

            mock_client.messages.create.side_effect = create
            messages = _screenshot_history(screenshot_count)

            await sampling_loop(
                model="kimi-for-coding",
                provider=APIProvider.KIMI,
                system_prompt_suffix="",
                messages=messages,
                output_callback=lambda _: None,
                tool_output_callback=lambda *_: None,
                api_response_callback=lambda *_: None,
                api_key="test-key",
                only_n_most_recent_images=3,
            )

            assert len(_nested_images(captured_messages[0])) == 3

        asyncio.run(run_test())


class TestResponseProcessor:
    """Test the ResponseProcessor class."""

    def test_process_response_text_only(self):
        """Test processing a response with only text content."""
        mock_response = Mock()
        mock_response.content = [
            Mock(type="text", text="This is a text response")
        ]

        processor = ResponseProcessor()
        result = processor.process_response(mock_response)

        assert result.has_text is True
        assert result.has_tools is False
        assert len(result.assistant_content) == 1
        assert result.assistant_content[0]["type"] == "text"
        assert result.assistant_content[0]["text"] == "This is a text response"
        assert len(result.tool_uses) == 0

    def test_process_response_tool_only(self):
        """Test processing a response with only tool use."""
        mock_response = Mock()
        mock_tool_use = Mock(
            type="tool_use",
            id="tool_123",
            name="browser",
            input={"action": "screenshot"}
        )
        mock_response.content = [mock_tool_use]

        processor = ResponseProcessor()
        result = processor.process_response(mock_response)

        assert result.has_text is False
        assert result.has_tools is True
        assert len(result.assistant_content) == 1
        assert result.assistant_content[0]["type"] == "tool_use"
        assert len(result.tool_uses) == 1

    def test_process_response_mixed_content(self):
        """Test processing a response with both text and tool use."""
        mock_response = Mock()
        mock_response.content = [
            Mock(type="text", text="Let me take a screenshot"),
            Mock(
                type="tool_use",
                id="tool_456",
                name="browser",
                input={"action": "screenshot"}
            )
        ]

        processor = ResponseProcessor()
        result = processor.process_response(mock_response)

        assert result.has_text is True
        assert result.has_tools is True
        assert len(result.assistant_content) == 2
        assert result.assistant_content[0]["type"] == "text"
        assert result.assistant_content[0]["text"] == "Let me take a screenshot"
        assert result.assistant_content[1]["type"] == "tool_use"
        assert len(result.tool_uses) == 1

    def test_process_response_preserves_signed_thinking_before_tool_use(self):
        thinking = Mock(type="thinking")
        thinking.model_dump.return_value = {
            "type": "thinking",
            "thinking": "inspect the current page",
            "signature": "signed-thinking",
        }
        tool_use = Mock(type="tool_use", id="tool_1", name="browser", input={})
        tool_use.model_dump.return_value = {
            "type": "tool_use",
            "id": "tool_1",
            "name": "browser",
            "input": {},
        }

        result = ResponseProcessor().process_response(
            Mock(content=[thinking, tool_use])
        )

        assert result.assistant_content[0]["signature"] == "signed-thinking"
        assert result.assistant_content[1]["id"] == "tool_1"

    def test_execute_tools_success(self):
        """Test successful tool execution."""

        async def run_test():
            mock_tool = AsyncMock(return_value=ToolResult(output="Tool executed"))
            mock_collection = Mock()
            mock_collection.tool_map = {"browser": mock_tool}

            tool_uses = [
                {
                    "type": "tool_use",
                    "id": "tool_789",
                    "name": "browser",
                    "input": {"action": "screenshot"}
                }
            ]

            processor = ResponseProcessor()
            results = await processor.execute_tools(tool_uses, mock_collection)

            assert len(results) == 1
            assert results[0]["type"] == "tool_result"
            assert results[0]["tool_use_id"] == "tool_789"
            assert any(
                block.get("text") == "Tool executed"
                for block in results[0]["content"]
            )

        asyncio.run(run_test())

    def test_execute_tools_with_error(self):
        """Test tool execution with error."""

        async def run_test():
            mock_tool = AsyncMock(side_effect=Exception("Tool failed"))
            mock_collection = Mock()
            mock_collection.tool_map = {"browser": mock_tool}

            tool_uses = [
                {
                    "type": "tool_use",
                    "id": "tool_error",
                    "name": "browser",
                    "input": {"action": "invalid"}
                }
            ]

            processor = ResponseProcessor()
            results = await processor.execute_tools(tool_uses, mock_collection)

            assert len(results) == 1
            assert results[0]["type"] == "tool_result"
            assert results[0]["is_error"] is True
            assert any(
                "Tool failed" in block.get("text", "")
                for block in results[0]["content"]
            )

        asyncio.run(run_test())

    def test_build_tool_result_with_image(self):
        """Test building tool result with base64 image."""
        result = ToolResult(base64_image="base64_data_here")
        processor = ResponseProcessor()

        tool_result = processor._build_tool_result(result, "tool_img")

        assert tool_result["type"] == "tool_result"
        assert tool_result["tool_use_id"] == "tool_img"
        assert any(
            block.get("type") == "image"
            for block in tool_result["content"]
        )

    def test_build_tool_result_with_text_extraction_markers(self):
        """Test handling of text extraction markers in tool results."""
        result = ToolResult(
            output="__PAGE_EXTRACTED__\nSome content\n__FULL_CONTENT__\nThe actual content"
        )
        processor = ResponseProcessor()

        tool_result = processor._build_tool_result(result, "tool_extract")

        assert any(
            block.get("text") == "The actual content"
            for block in tool_result["content"]
        )


class TestMessageBuilder:
    """Test the MessageBuilder class."""

    def test_add_assistant_message(self):
        """Test adding an assistant message."""
        messages = []
        content = [
            {"type": "text", "text": "Hello"},
            {"type": "tool_use", "id": "123", "name": "test", "input": {}}
        ]

        builder = MessageBuilder()
        builder.add_assistant_message(messages, content)

        assert len(messages) == 1
        assert messages[0]["role"] == "assistant"
        assert messages[0]["content"] == content

    def test_add_assistant_message_empty_content(self):
        """Test that empty content is not added."""
        messages = []
        content = []

        builder = MessageBuilder()
        builder.add_assistant_message(messages, content)

        assert len(messages) == 0

    def test_add_tool_results(self):
        """Test adding tool results."""
        messages = []
        tool_results = [
            {
                "type": "tool_result",
                "tool_use_id": "123",
                "content": [{"type": "text", "text": "Result"}]
            }
        ]

        builder = MessageBuilder()
        builder.add_tool_results(messages, tool_results)

        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == tool_results

    def test_add_tool_results_empty(self):
        """Test that empty tool results are not added."""
        messages = []
        tool_results = []

        builder = MessageBuilder()
        builder.add_tool_results(messages, tool_results)

        assert len(messages) == 0

    def test_ensure_message_integrity_valid(self):
        """Test message integrity validation with valid messages."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": [{"type": "text", "text": "Hi"}]},
        ]

        builder = MessageBuilder()
        assert builder.ensure_message_integrity(messages) is True

    def test_ensure_message_integrity_missing_role(self):
        """Test message integrity with missing role."""
        messages = [
            {"content": "Hello"},
        ]

        builder = MessageBuilder()
        assert builder.ensure_message_integrity(messages) is False

    def test_ensure_message_integrity_missing_content(self):
        """Test message integrity with missing content."""
        messages = [
            {"role": "user"},
        ]

        builder = MessageBuilder()
        assert builder.ensure_message_integrity(messages) is False

    def test_ensure_message_integrity_empty_list_content(self):
        """Test message integrity with empty content list."""
        messages = [
            {"role": "user", "content": []},
        ]

        builder = MessageBuilder()
        assert builder.ensure_message_integrity(messages) is False

    def test_extract_text_from_message(self):
        """Test extracting text from assistant message."""
        message = {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "First part"},
                {"type": "tool_use", "id": "123", "name": "test", "input": {}},
                {"type": "text", "text": "Second part"}
            ]
        }

        builder = MessageBuilder()
        text = builder.extract_text_from_message(message)

        assert text == "First part Second part"

    def test_extract_text_from_message_no_text(self):
        """Test extracting text when there's no text content."""
        message = {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "123", "name": "test", "input": {}}
            ]
        }

        builder = MessageBuilder()
        text = builder.extract_text_from_message(message)

        assert text is None

    def test_extract_text_from_user_message(self):
        """Test that text extraction returns None for non-assistant messages."""
        message = {
            "role": "user",
            "content": "User message"
        }

        builder = MessageBuilder()
        text = builder.extract_text_from_message(message)

        assert text is None


@pytest.mark.integration
class TestSamplingLoopIntegration:
    """Integration tests for the sampling loop."""

    @patch("browser_use_demo.loop.Anthropic")
    def test_kimi_uses_standard_messages_without_betas(self, mock_anthropic):
        async def run_test():
            mock_client = Mock()
            mock_anthropic.return_value = mock_client
            mock_client.messages.create.return_value = Mock(
                content=[Mock(type="text", text="done")]
            )

            await sampling_loop(
                model="kimi-for-coding",
                provider=APIProvider.KIMI,
                system_prompt_suffix="",
                messages=[{"role": "user", "content": "Test"}],
                output_callback=lambda _: None,
                tool_output_callback=lambda *_: None,
                api_response_callback=lambda *_: None,
                api_key="test-key",
                base_url="https://example.invalid/",
            )

            mock_anthropic.assert_called_once_with(
                api_key="test-key",
                base_url="https://example.invalid/",
                max_retries=4,
            )
            mock_client.messages.create.assert_called_once()
            mock_client.beta.messages.create.assert_not_called()
            kwargs = mock_client.messages.create.call_args.kwargs
            assert "betas" not in kwargs
            assert "cache_control" not in kwargs["system"][0]

        asyncio.run(run_test())

    @patch("browser_use_demo.loop.Anthropic")
    def test_max_tokens_is_rejected_before_turn_is_accepted(self, mock_anthropic):
        async def run_test():
            mock_client = Mock()
            mock_anthropic.return_value = mock_client
            mock_client.messages.create.return_value = Mock(
                content=[Mock(type="text", text="partial")],
                stop_reason="max_tokens",
            )
            messages = [{"role": "user", "content": "Test"}]

            with pytest.raises(RuntimeError, match="reached max_tokens"):
                await sampling_loop(
                    model="kimi-for-coding",
                    provider=APIProvider.KIMI,
                    system_prompt_suffix="",
                    messages=messages,
                    output_callback=lambda _: None,
                    tool_output_callback=lambda *_: None,
                    api_response_callback=lambda *_: None,
                    api_key="test-key",
                )

            assert messages == [{"role": "user", "content": "Test"}]

        asyncio.run(run_test())

    @pytest.mark.parametrize("invalid_limit", [0, -1, True, 1.5])
    @patch("browser_use_demo.loop.Anthropic")
    def test_max_tool_turns_requires_positive_integer(
        self,
        mock_anthropic,
        invalid_limit,
    ):
        async def run_test():
            with pytest.raises(
                ValueError,
                match="max_tool_turns must be a positive integer",
            ):
                await sampling_loop(
                    model="kimi-for-coding",
                    provider=APIProvider.KIMI,
                    system_prompt_suffix="",
                    messages=[{"role": "user", "content": "Test"}],
                    output_callback=lambda _: None,
                    tool_output_callback=lambda *_: None,
                    api_response_callback=lambda *_: None,
                    api_key="test-key",
                    max_tool_turns=invalid_limit,
                )

            mock_anthropic.assert_not_called()

        asyncio.run(run_test())

    @patch("browser_use_demo.loop.Anthropic")
    def test_tool_turn_limit_stops_before_unpaired_extra_tool_use(
        self,
        mock_anthropic,
    ):
        async def run_test():
            mock_client = Mock()
            mock_anthropic.return_value = mock_client
            mock_client.messages.create.side_effect = [
                Mock(
                    content=[
                        ToolUseBlock(
                            type="tool_use",
                            id="tool_1",
                            name="browser",
                            input={"action": "screenshot"},
                        )
                    ],
                    stop_reason="tool_use",
                ),
                Mock(
                    content=[
                        ToolUseBlock(
                            type="tool_use",
                            id="tool_2",
                            name="browser",
                            input={"action": "screenshot"},
                        )
                    ],
                    stop_reason="tool_use",
                ),
            ]
            browser = AsyncMock()
            browser.name = "browser"
            browser.to_params = Mock(return_value={})
            browser.return_value = ToolResult(output="screenshot complete")
            messages = [{"role": "user", "content": "Keep browsing"}]

            with pytest.raises(RuntimeError, match=r"tool turn limit reached \(1\)"):
                await sampling_loop(
                    model="kimi-for-coding",
                    provider=APIProvider.KIMI,
                    system_prompt_suffix="",
                    messages=messages,
                    output_callback=lambda _: None,
                    tool_output_callback=lambda *_: None,
                    api_response_callback=lambda *_: None,
                    api_key="test-key",
                    max_tool_turns=1,
                    browser_tool=browser,
                )

            assert browser.await_count == 1
            tool_use_ids = [
                block["id"]
                for message in messages
                if message["role"] == "assistant"
                for block in message["content"]
                if block["type"] == "tool_use"
            ]
            tool_result_ids = [
                block["tool_use_id"]
                for message in messages
                if message["role"] == "user" and isinstance(message["content"], list)
                for block in message["content"]
                if block["type"] == "tool_result"
            ]
            assert tool_use_ids == ["tool_1"]
            assert tool_result_ids == tool_use_ids

        asyncio.run(run_test())

    @patch("browser_use_demo.loop.Anthropic")
    def test_normal_text_response_can_finish_after_last_tool_turn(
        self,
        mock_anthropic,
    ):
        async def run_test():
            mock_client = Mock()
            mock_anthropic.return_value = mock_client
            mock_client.messages.create.side_effect = [
                Mock(
                    content=[
                        ToolUseBlock(
                            type="tool_use",
                            id="tool_1",
                            name="browser",
                            input={"action": "screenshot"},
                        )
                    ],
                    stop_reason="tool_use",
                ),
                Mock(
                    content=[TextBlock(type="text", text="Finished")],
                    stop_reason="end_turn",
                ),
            ]
            browser = AsyncMock()
            browser.name = "browser"
            browser.to_params = Mock(return_value={})
            browser.return_value = ToolResult(output="screenshot complete")

            messages = await sampling_loop(
                model="kimi-for-coding",
                provider=APIProvider.KIMI,
                system_prompt_suffix="",
                messages=[{"role": "user", "content": "Take one screenshot"}],
                output_callback=lambda _: None,
                tool_output_callback=lambda *_: None,
                api_response_callback=lambda *_: None,
                api_key="test-key",
                max_tool_turns=1,
                browser_tool=browser,
            )

            assert browser.await_count == 1
            assert messages[-1] == {
                "role": "assistant",
                "content": [{"text": "Finished", "type": "text"}],
            }

        asyncio.run(run_test())

    @patch("browser_use_demo.loop.Anthropic")
    def test_sampling_loop_preserves_text_with_tools(self, mock_anthropic):
        """Test that text is preserved when tools are used."""

        async def run_test():
            mock_client = Mock()
            mock_anthropic.return_value = mock_client

            tool_response = Mock()
            tool_response.content = [
                TextBlock(type="text", text="I'll help you with that"),
                ToolUseBlock(
                    type="tool_use",
                    id="tool_001",
                    name="browser",
                    input={"action": "screenshot"}
                )
            ]
            tool_response.stop_reason = "tool_use"
            final_response = Mock(
                content=[TextBlock(type="text", text="Done")],
                stop_reason="end_turn",
            )

            mock_client.beta.messages.create = Mock(
                side_effect=[tool_response, final_response]
            )

            mock_browser = AsyncMock()
            mock_browser.return_value = ToolResult(output="Screenshot taken")

            messages = [{"role": "user", "content": "Take a screenshot"}]
            output_messages = []
            tool_outputs = {}

            def output_callback(content):
                output_messages.append(content)

            def tool_output_callback(result, tool_id):
                tool_outputs[tool_id] = result

            updated_messages = await sampling_loop(
                model="claude-sonnet-4-5",
                provider=APIProvider.ANTHROPIC,
                system_prompt_suffix="",
                messages=messages,
                output_callback=output_callback,
                tool_output_callback=tool_output_callback,
                api_response_callback=lambda *args: None,
                api_key="test_key",
                browser_tool=mock_browser
            )

            assert len(output_messages) >= 2
            assert any(
                msg.get("type") == "text" and "help you" in msg.get("text", "")
                for msg in output_messages
            )
            assert any(msg.get("type") == "tool_use" for msg in output_messages)

            assistant_msgs = [m for m in updated_messages if m["role"] == "assistant"]
            assert len(assistant_msgs) > 0

            mixed_assistant = next(
                message
                for message in assistant_msgs
                if any(
                    block.get("type") == "tool_use"
                    for block in message["content"]
                )
            )
            assert isinstance(mixed_assistant["content"], list)

            has_text = any(
                block.get("type") == "text"
                for block in mixed_assistant["content"]
            )
            has_tool = any(
                block.get("type") == "tool_use"
                for block in mixed_assistant["content"]
            )

            assert has_text and has_tool, "Assistant message should contain both text and tool use"

        asyncio.run(run_test())

    @patch("browser_use_demo.loop.Anthropic")
    def test_sampling_loop_text_only_response(self, mock_anthropic):
        """Test handling of text-only responses."""

        async def run_test():
            mock_client = Mock()
            mock_anthropic.return_value = mock_client

            mock_response = Mock()
            mock_response.content = [
                Mock(type="text", text="This is just a text response")
            ]

            mock_client.beta.messages.create = Mock(return_value=mock_response)

            messages = [{"role": "user", "content": "Hello"}]

            updated_messages = await sampling_loop(
                model="claude-sonnet-4-5",
                provider=APIProvider.ANTHROPIC,
                system_prompt_suffix="",
                messages=messages,
                output_callback=lambda x: None,
                tool_output_callback=lambda r, i: None,
                api_response_callback=lambda *args: None,
                api_key="test_key"
            )

            assert len(updated_messages) == 2
            assert updated_messages[-1]["role"] == "assistant"
            assert any(
                block.get("text") == "This is just a text response"
                for block in updated_messages[-1]["content"]
            )

        asyncio.run(run_test())

    @patch("browser_use_demo.loop.Anthropic")
    def test_sampling_loop_multiple_tools_with_text(self, mock_anthropic):
        """Test handling of multiple tool uses with text."""

        async def run_test():
            mock_client = Mock()
            mock_anthropic.return_value = mock_client

            tool_response = Mock()
            tool_response.content = [
                TextBlock(type="text", text="I'll perform multiple actions"),
                ToolUseBlock(
                    type="tool_use",
                    id="tool_001",
                    name="browser",
                    input={"action": "screenshot"}
                ),
                TextBlock(type="text", text="Now navigating"),
                ToolUseBlock(
                    type="tool_use",
                    id="tool_002",
                    name="browser",
                    input={"action": "navigate", "url": "example.com"}
                )
            ]
            tool_response.stop_reason = "tool_use"
            final_response = Mock(
                content=[TextBlock(type="text", text="Done")],
                stop_reason="end_turn",
            )

            mock_client.beta.messages.create = Mock(
                side_effect=[tool_response, final_response]
            )

            mock_browser = AsyncMock()
            mock_browser.return_value = ToolResult(output="Action completed")

            messages = [{"role": "user", "content": "Do multiple things"}]

            updated_messages = await sampling_loop(
                model="claude-sonnet-4-5",
                provider=APIProvider.ANTHROPIC,
                system_prompt_suffix="",
                messages=messages,
                output_callback=lambda x: None,
                tool_output_callback=lambda r, i: None,
                api_response_callback=lambda *args: None,
                api_key="test_key",
                browser_tool=mock_browser
            )

            assistant_msgs = [m for m in updated_messages if m["role"] == "assistant"]
            mixed_assistant = next(
                message
                for message in assistant_msgs
                if any(
                    block.get("type") == "tool_use"
                    for block in message["content"]
                )
            )

            text_blocks = [
                block for block in mixed_assistant["content"]
                if block.get("type") == "text"
            ]
            tool_blocks = [
                block for block in mixed_assistant["content"]
                if block.get("type") == "tool_use"
            ]

            assert len(text_blocks) == 2
            assert len(tool_blocks) == 2

        asyncio.run(run_test())

    @patch("browser_use_demo.loop.Anthropic")
    def test_tools_use_default_auto_tool_choice(self, mock_anthropic):
        """Providing tools without tool_choice uses the API's default auto mode."""

        async def run_test():
            mock_client = Mock()
            mock_anthropic.return_value = mock_client

            mock_response = Mock()
            mock_response.content = [Mock(type="text", text="Response")]

            mock_client.beta.messages.create = Mock(return_value=mock_response)

            await sampling_loop(
                model="claude-sonnet-4-5",
                provider=APIProvider.ANTHROPIC,
                system_prompt_suffix="",
                messages=[{"role": "user", "content": "Test"}],
                output_callback=lambda x: None,
                tool_output_callback=lambda r, i: None,
                api_response_callback=lambda *args: None,
                api_key="test_key"
            )

            call_args = mock_client.beta.messages.create.call_args[1]
            assert call_args["tools"]
            assert "tool_choice" not in call_args

        asyncio.run(run_test())
