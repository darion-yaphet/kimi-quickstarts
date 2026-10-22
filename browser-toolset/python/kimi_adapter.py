"""Run ordinary Anthropic-compatible tool calls through the browser toolset driver."""

from __future__ import annotations

import json
from typing import Any

from anthropic import Anthropic
from anthropic.tools.browser import BetaAbstractBrowserToolset20260801
from anthropic.types.beta import BetaToolResultBlockParam, BetaToolUseBlock


BROWSER_TOOLS: list[dict[str, Any]] = [
    {
        "name": "navigate",
        "description": "Navigate the active browser tab to an HTTP(S) URL allowed by the local URL policy.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "URL to open"}},
            "required": ["url"],
            "additionalProperties": False,
        },
    },
    {
        "name": "read_page",
        "description": "List visible headings, links, buttons and form controls with refs for clicking.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "left_click",
        "description": "Left-click a current element ref from read_page, or a viewport coordinate.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {
                    "oneOf": [
                        {
                            "type": "object",
                            "properties": {"type": {"const": "ref"}, "ref": {"type": "string"}},
                            "required": ["type", "ref"],
                            "additionalProperties": False,
                        },
                        {
                            "type": "object",
                            "properties": {
                                "type": {"const": "coordinate"},
                                "x": {"type": "number"},
                                "y": {"type": "number"},
                            },
                            "required": ["type", "x", "y"],
                            "additionalProperties": False,
                        },
                    ]
                }
            },
            "required": ["target"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_page_text",
        "description": "Return the visible text of the current page.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "wait",
        "description": "Wait up to 30 seconds for the page to change.",
        "input_schema": {
            "type": "object",
            "properties": {"duration": {"type": "number", "minimum": 0, "maximum": 30}},
            "required": ["duration"],
            "additionalProperties": False,
        },
    },
]

MAX_OUTPUT_TOKENS = 8192


def dispatch(
    browser: BetaAbstractBrowserToolset20260801, tool_use_id: str, name: str, input: dict[str, Any]
) -> BetaToolResultBlockParam:
    """Use the SDK driver entry point so its URL policy and validation still run."""
    call = BetaToolUseBlock(type="tool_use", id=tool_use_id, name=name, input=input, toolset_name="browser")
    return browser.tool_result(call)


def portable_content(result: BetaToolResultBlockParam) -> str | list[dict[str, Any]]:
    """Translate browser-toolset-only result blocks to ordinary text/image content."""
    content = result.get("content", "")
    if isinstance(content, str):
        return content
    portable: list[dict[str, Any]] = []
    for block in content:
        block_dict = dict(block)
        if block_dict.get("type") == "browser_state":
            state = {key: value for key, value in block_dict.items() if key != "type"}
            portable.append(
                {
                    "type": "text",
                    "text": "Browser state: " + json.dumps(state, ensure_ascii=False, separators=(",", ":")),
                }
            )
        else:
            portable.append(block_dict)
    return portable


def run_loop(
    client: Anthropic,
    browser: BetaAbstractBrowserToolset20260801,
    task: str,
    *,
    model: str,
    max_turns: int = 20,
) -> None:
    """Run a bounded Messages API tool loop and print each model response."""
    messages: list[dict[str, Any]] = [{"role": "user", "content": task}]
    for _ in range(max_turns):
        response = client.messages.create(
            model=model,
            max_tokens=MAX_OUTPUT_TOKENS,
            tools=BROWSER_TOOLS,
            messages=messages,
        )
        print(response)
        if response.stop_reason == "max_tokens":
            raise RuntimeError(
                f"model response was truncated at {MAX_OUTPUT_TOKENS} output tokens before the tool turn completed"
            )
        messages.append(
            {"role": "assistant", "content": [block.model_dump(exclude_none=True) for block in response.content]}
        )
        calls = [block for block in response.content if block.type == "tool_use"]
        if not calls:
            return
        results: list[dict[str, Any]] = []
        for call in calls:
            result = dispatch(browser, call.id, call.name, call.input)
            tool_result: dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": call.id,
                "content": portable_content(result),
            }
            if result.get("is_error"):
                tool_result["is_error"] = True
            results.append(tool_result)
        messages.append({"role": "user", "content": results})
    raise RuntimeError(f"model did not finish within {max_turns} tool turns")
