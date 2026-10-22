"""Deterministic, offline responses for trying the local session service."""

from __future__ import annotations

import json

from .profiles import PROFILES


DEMO_PREFIX = "OFFLINE DEMO — simulated response"
_EVIDENCE_MARKER = '"collection"'

_PROFILE_RESPONSES = {
    "assistant-ui": "I would help analyze the supplied data and explain the calculation steps.",
    "chat-sdk": "I would answer from the conversation and flag anything that needs current research.",
    "copilot-kit-ag-ui": "I would provide educational financial analysis with explicit assumptions.",
    "daily-brief": "I would draft a concise brief from the material supplied in this session.",
    "knowledge-wiki": "I would answer from locally retrieved documents and identify evidence gaps.",
    "linear": "I would draft an issue response from the supplied issue context.",
    "mcp-server-typescript": "I would give a concise answer using the conversation context.",
    "roadtrip-planner": "I would draft a route from the supplied constraints and flag facts needing live data.",
    "self-hosted-sandboxes": "I would describe the requested Python task without executing it.",
    "sentry": "I would triage the supplied error data by likely user impact.",
    "slack": "I would draft a helpful reply from the supplied thread context.",
}




class DemoProvider:
    """Provider with no credentials, network requests, or side effects."""

    mode = "demo"

    def complete(self, system: str, messages: list[dict]) -> str:
        profile = self._profile(system)
        prompt = self._last_user_text(messages)
        sources, has_collection = self._sources(system)

        lines = [DEMO_PREFIX, _PROFILE_RESPONSES[profile]]
        if prompt:
            lines.append(f"Request received: {prompt}")
        if sources:
            lines.append("Supplied local evidence:")
            for source in sources[:3]:
                content = " ".join(source["content"].split())
                lines.append(f"- {content} [{source['citation_id']}]")
        elif has_collection:
            lines.append("No relevant local evidence was supplied for this request.")
        lines.append("This demo did not contact a model or an external service.")
        return "\n".join(lines)

    def complete_with_tools(
        self, system, messages, *, tools, execute, max_turns=8
    ) -> str:
        reply = self.complete(system, messages)
        if tools:
            reply += "\nNo tools were executed in offline demo mode."
        return reply

    @staticmethod
    def _profile(system: str) -> str:
        for name, profile in PROFILES.items():
            if profile.instructions in system:
                return name
        return "mcp-server-typescript"

    @staticmethod
    def _last_user_text(messages: list[dict]) -> str:
        for message in reversed(messages):
            if message.get("role") != "user":
                continue
            content = message.get("content", "")
            if isinstance(content, str):
                # Avoid rendering bracketed user text as if it were an evidence citation.
                return " ".join(content.split()).replace("[", "(").replace("]", ")")
        return ""

    @staticmethod
    def _sources(system: str) -> tuple[list[dict[str, str]], bool]:
        marker = system.find(_EVIDENCE_MARKER)
        if marker < 0:
            return [], False
        start = system.rfind("{", 0, marker + 1)
        if start < 0:
            return [], False
        try:
            payload, _ = json.JSONDecoder().raw_decode(system[start:])
        except (json.JSONDecodeError, TypeError):
            return [], False
        if not isinstance(payload, dict) or "collection" not in payload:
            return [], False
        sources = []
        for source in payload.get("sources", []):
            if not isinstance(source, dict):
                continue
            citation_id = source.get("citation_id")
            content = source.get("content")
            if isinstance(citation_id, str) and isinstance(content, str):
                sources.append({"citation_id": citation_id, "content": content})
        return sources, True
