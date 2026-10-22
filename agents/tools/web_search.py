"""Portable web-search tool with an explicit user-supplied backend."""

import asyncio
import inspect
import json
import os
import urllib.request
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse

from .base import Tool

SearchResults = list[dict[str, Any]]
SearchBackend = Callable[..., SearchResults | Awaitable[SearchResults]]


def _http_json_backend(endpoint: str, api_key: str | None = None) -> SearchBackend:
    """Create a JSON-over-HTTP adapter for a user-controlled service."""
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("WEB_SEARCH_BACKEND_URL must be an absolute HTTP(S) URL")

    def search(**payload: Any) -> SearchResults:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            body = json.loads(response.read().decode("utf-8"))
        results = body.get("results") if isinstance(body, dict) else body
        if not isinstance(results, list) or not all(
            isinstance(item, dict) for item in results
        ):
            raise ValueError("search backend must return a list of result objects")
        return results

    return search


class WebSearchServerTool(Tool):
    """Search through a configured callable or JSON HTTP backend.

    The historical class name is retained for import compatibility. No
    provider-hosted search type or beta header is emitted.
    """

    def __init__(
        self,
        name: str = "web_search",
        *,
        backend: SearchBackend | None = None,
        max_uses: int | None = None,
        allowed_domains: list[str] | None = None,
        blocked_domains: list[str] | None = None,
        user_location: dict[str, Any] | None = None,
        default_max_results: int = 5,
    ) -> None:
        if max_uses is not None and max_uses <= 0:
            raise ValueError("max_uses must be positive")
        if default_max_results <= 0:
            raise ValueError("default_max_results must be positive")
        self.max_uses = max_uses
        self.allowed_domains = allowed_domains
        self.blocked_domains = blocked_domains
        self.user_location = user_location
        self.default_max_results = default_max_results
        self._uses = 0
        self._lock = asyncio.Lock()

        endpoint = os.environ.get("WEB_SEARCH_BACKEND_URL")
        self.backend = backend or (
            _http_json_backend(endpoint, os.environ.get("WEB_SEARCH_API_KEY"))
            if endpoint
            else None
        )

        super().__init__(
            name=name,
            description=(
                "Search the web through the application's configured search "
                "backend and return title, URL, and snippet fields."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query."},
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                        "description": "Maximum number of results to return.",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        )

    @staticmethod
    def _matches_domain(hostname: str, domain: str) -> bool:
        normalized = domain.lower().lstrip(".")
        return hostname == normalized or hostname.endswith(f".{normalized}")

    def _filter_results(self, results: SearchResults) -> SearchResults:
        filtered: SearchResults = []
        for result in results:
            hostname = (urlparse(str(result.get("url", ""))).hostname or "").lower()
            if self.allowed_domains and not any(
                self._matches_domain(hostname, domain)
                for domain in self.allowed_domains
            ):
                continue
            if self.blocked_domains and any(
                self._matches_domain(hostname, domain)
                for domain in self.blocked_domains
            ):
                continue
            filtered.append(result)
        return filtered

    async def execute(self, query: str, max_results: int | None = None) -> str:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        if self.backend is None:
            raise RuntimeError(
                "No search backend configured; pass backend=... or set "
                "WEB_SEARCH_BACKEND_URL"
            )
        limit = max_results or self.default_max_results
        if not 1 <= limit <= 20:
            raise ValueError("max_results must be between 1 and 20")

        async with self._lock:
            if self.max_uses is not None and self._uses >= self.max_uses:
                raise RuntimeError(f"web search limit of {self.max_uses} uses reached")
            self._uses += 1

        kwargs = {
            "query": query.strip(),
            "max_results": limit,
            "allowed_domains": self.allowed_domains,
            "blocked_domains": self.blocked_domains,
            "user_location": self.user_location,
        }
        if inspect.iscoroutinefunction(self.backend):
            result = await self.backend(**kwargs)
        else:
            result = await asyncio.to_thread(self.backend, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, list) or not all(
            isinstance(item, dict) for item in result
        ):
            raise ValueError("search backend must return a list of result objects")

        filtered = self._filter_results(result)[:limit]
        return json.dumps(filtered, ensure_ascii=False, indent=2)
