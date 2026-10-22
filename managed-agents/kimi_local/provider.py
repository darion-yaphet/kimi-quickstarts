"""Kimi Code provider using the Anthropic-compatible Python SDK."""

import os
import math


class KimiProvider:
    def __init__(self):
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is required for chat. Use a Kimi Code API key."
            )
        try:
            from anthropic import Anthropic
        except ImportError as error:
            raise RuntimeError(
                "Install the local runtime dependency with: "
                "python -m pip install -r requirements.txt"
            ) from error

        self.model = os.environ.get("ANTHROPIC_MODEL", "kimi-for-coding")
        self.max_tokens = int(os.environ.get("ANTHROPIC_MAX_TOKENS", "4096"))
        self.timeout_seconds = float(os.environ.get("ANTHROPIC_TIMEOUT_SECONDS", "120"))
        if self.max_tokens <= 0:
            raise RuntimeError("ANTHROPIC_MAX_TOKENS must be greater than zero")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise RuntimeError("ANTHROPIC_TIMEOUT_SECONDS must be greater than zero")
        self.client = Anthropic(
            api_key=api_key,
            base_url=os.environ.get(
                "ANTHROPIC_BASE_URL", "https://api.kimi.com/coding/"
            ),
            timeout=self.timeout_seconds,
        )

    def complete(self, system: str, messages: list[dict]) -> str:
        return self.complete_with_tools(system, messages, tools=[], execute=None)

    def complete_with_tools(
        self, system, messages, *, tools, execute, max_turns=8
    ) -> str:
        import json

        history = list(messages)
        if (
            not isinstance(max_turns, int)
            or isinstance(max_turns, bool)
            or not 1 <= max_turns <= 128
        ):
            raise ValueError("max_turns must be between 1 and 128")
        for turn in range(max_turns + 1):
            request = dict(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=history,
            )
            if tools:
                request["tools"] = tools
                if turn == max_turns:
                    request["tool_choice"] = {"type": "none"}
            response = self.client.messages.create(**request)
            if response.stop_reason == "max_tokens":
                raise RuntimeError(
                    "Kimi response was truncated at ANTHROPIC_MAX_TOKENS; increase the limit and retry"
                )
            calls = [block for block in response.content if block.type == "tool_use"]
            if not calls:
                text = "\n".join(
                    block.text for block in response.content if block.type == "text"
                ).strip()
                if not text:
                    raise RuntimeError("Kimi returned no text content")
                return text
            if execute is None:
                raise RuntimeError("Kimi requested a tool when none were configured")
            if turn == max_turns:
                raise RuntimeError(
                    "Kimi exceeded the configured tool-turn limit; no further tools were executed"
                )
            history.append(
                {
                    "role": "assistant",
                    "content": [
                        block.model_dump(exclude_none=True)
                        for block in response.content
                    ],
                }
            )
            results = []
            for call in calls:
                try:
                    outcome = execute(call.name, call.input)
                    result = {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": json.dumps(outcome, ensure_ascii=False),
                    }
                except Exception as error:
                    result = {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": str(error),
                        "is_error": True,
                    }
                results.append(result)
            history.append({"role": "user", "content": results})
        raise RuntimeError("Kimi exceeded the configured tool-turn limit")
