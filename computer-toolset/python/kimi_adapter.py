"""Run ordinary Anthropic-compatible tool calls through the VNC computer driver."""

from __future__ import annotations

from typing import Any

from anthropic import Anthropic
from anthropic.tools.computer import BetaAbstractComputerToolset20260801
from anthropic.types.beta import BetaToolResultBlockParam, BetaToolUseBlock


COORDINATE = {
    "type": "array",
    "items": {"type": "integer"},
    "minItems": 2,
    "maxItems": 2,
    "description": "[x, y] screen coordinate",
}
CLICK_PROPERTIES = {
    "coordinate": COORDINATE,
    "text": {"type": "string", "description": "Optional modifier chord such as ctrl or ctrl+shift"},
}


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        schema["required"] = required
    return {"name": name, "description": description, "input_schema": schema}


COMPUTER_TOOLS = [
    _tool("screenshot", "Capture the whole VNC desktop as an image.", {}),
    _tool("left_click", "Left-click at a coordinate or the last pointer position.", CLICK_PROPERTIES),
    _tool("right_click", "Right-click at a coordinate or the last pointer position.", CLICK_PROPERTIES),
    _tool("double_click", "Double-click at a coordinate or the last pointer position.", CLICK_PROPERTIES),
    _tool("triple_click", "Triple-click at a coordinate or the last pointer position.", CLICK_PROPERTIES),
    _tool("mouse_move", "Move the pointer to a screen coordinate.", {"coordinate": COORDINATE}, ["coordinate"]),
    _tool(
        "scroll",
        "Scroll at a coordinate or the last pointer position.",
        {
            **CLICK_PROPERTIES,
            "scroll_direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
            "scroll_amount": {"type": "integer", "minimum": 1, "maximum": 10},
        },
        ["scroll_direction", "scroll_amount"],
    ),
    _tool(
        "key",
        "Press a key, named key, or chord such as Return, Page_Up, or ctrl+s.",
        {"text": {"type": "string"}, "repeat": {"type": "integer", "minimum": 1, "maximum": 20}},
        ["text"],
    ),
    _tool("type", "Type text into the focused application.", {"text": {"type": "string"}}, ["text"]),
    _tool(
        "wait",
        "Wait briefly for the desktop to update.",
        {"duration": {"type": "number", "minimum": 0, "maximum": 30}},
        ["duration"],
    ),
]

MAX_OUTPUT_TOKENS = 8192


def dispatch(
    computer: BetaAbstractComputerToolset20260801, tool_use_id: str, name: str, input: dict[str, Any]
) -> BetaToolResultBlockParam:
    """Use the SDK driver entry point so validation and its confirmation hook still run."""
    call = BetaToolUseBlock(type="tool_use", id=tool_use_id, name=name, input=input, toolset_name="computer")
    return computer.tool_result(call)


def run_loop(
    client: Anthropic,
    computer: BetaAbstractComputerToolset20260801,
    task: str,
    *,
    model: str,
    max_turns: int = 30,
) -> None:
    """Run a bounded Messages API tool loop and print each model response."""
    messages: list[dict[str, Any]] = [{"role": "user", "content": task}]
    for _ in range(max_turns):
        response = client.messages.create(
            model=model,
            max_tokens=MAX_OUTPUT_TOKENS,
            tools=COMPUTER_TOOLS,
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
            result = dispatch(computer, call.id, call.name, call.input)
            tool_result: dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": call.id,
                "content": result.get("content", ""),
            }
            if result.get("is_error"):
                tool_result["is_error"] = True
            results.append(tool_result)
        messages.append({"role": "user", "content": results})
    raise RuntimeError(f"model did not finish within {max_turns} tool turns")
