"""Kimi Code provider using the Anthropic-compatible Python SDK."""

import os


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
        self.timeout_seconds = float(
            os.environ.get("ANTHROPIC_TIMEOUT_SECONDS", "120")
        )
        if self.max_tokens <= 0:
            raise RuntimeError("ANTHROPIC_MAX_TOKENS must be greater than zero")
        if self.timeout_seconds <= 0:
            raise RuntimeError("ANTHROPIC_TIMEOUT_SECONDS must be greater than zero")
        self.client = Anthropic(
            api_key=api_key,
            base_url=os.environ.get(
                "ANTHROPIC_BASE_URL", "https://api.kimi.com/coding/"
            ),
            timeout=self.timeout_seconds,
        )

    def complete(self, system: str, messages: list[dict]) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=messages,
        )
        if response.stop_reason == "max_tokens":
            raise RuntimeError(
                "Kimi response was truncated at ANTHROPIC_MAX_TOKENS; "
                "increase the limit and retry"
            )
        text = "\n".join(
            block.text for block in response.content if block.type == "text"
        ).strip()
        if not text:
            raise RuntimeError("Kimi returned no text content")
        return text
