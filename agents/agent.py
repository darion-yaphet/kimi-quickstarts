"""Agent implementation with an Anthropic-compatible API and tools."""

import asyncio
import os
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from anthropic import Anthropic

from .tools.base import Tool
from .utils.connections import setup_mcp_connections
from .utils.history_util import MessageHistory
from .utils.tool_util import execute_tools


DEFAULT_BASE_URL = "https://api.kimi.com/coding/"
DEFAULT_MODEL = "kimi-for-coding"


@dataclass
class ModelConfig:
    """Configuration settings for model parameters."""

    model: str = field(
        default_factory=lambda: os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL)
    )
    max_tokens: int = 4096
    context_window_tokens: int = field(
        default_factory=lambda: int(
            os.environ.get("ANTHROPIC_CONTEXT_WINDOW_TOKENS", "262144")
        )
    )
    enable_prompt_caching: bool = False


class Agent:
    """Anthropic-compatible agent with tool use capabilities."""

    def __init__(
        self,
        name: str,
        system: str,
        tools: list[Tool] | None = None,
        mcp_servers: list[dict[str, Any]] | None = None,
        config: ModelConfig | None = None,
        verbose: bool = False,
        client: Anthropic | None = None,
        message_params: dict[str, Any] | None = None,
    ):
        """Initialize an Agent.
        
        Args:
            name: Agent identifier for logging
            system: System prompt for the agent
            tools: List of tools available to the agent
            mcp_servers: MCP server configurations
            config: Model configuration with defaults
            verbose: Enable detailed logging
            client: Anthropic client instance
            message_params: Additional parameters for client.messages.create().
                           These override any conflicting parameters from config.
        """
        self.name = name
        self.system = system
        self.verbose = verbose
        self.tools = list(tools or [])
        self.config = config or ModelConfig()
        self.mcp_servers = mcp_servers or []
        self.message_params = message_params or {}
        self.client = client or Anthropic(
            api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
            base_url=os.environ.get("ANTHROPIC_BASE_URL", DEFAULT_BASE_URL),
        )
        self.history = MessageHistory(
            model=self.config.model,
            system=self.system,
            context_window_tokens=self.config.context_window_tokens,
            client=self.client,
            enable_caching=self.config.enable_prompt_caching,
        )

        if self.verbose:
            print(f"\n[{self.name}] Agent initialized")

    def _prepare_message_params(self) -> dict[str, Any]:
        """Prepare parameters for client.messages.create() call.
        
        Returns a dict with base parameters from config, with any
        message_params overriding conflicting keys.
        """
        return {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "system": self.system,
            "messages": self.history.format_for_api(),
            "tools": [tool.to_dict() for tool in self.tools],
            **self.message_params,
        }

    async def _agent_loop(self, user_input: str) -> list[dict[str, Any]]:
        """Process user input and handle tool calls in a loop"""
        if self.verbose:
            print(f"\n[{self.name}] Received: {user_input}")
        await self.history.add_message("user", user_input, None)

        tool_dict = {tool.name: tool for tool in self.tools}

        while True:
            self.history.truncate()
            params = self._prepare_message_params()

            # Provider-specific beta headers may be supplied explicitly through
            # message_params. Portable custom tools need no beta header.
            response = self.client.messages.create(**params)
            tool_calls = [
                block for block in response.content if block.type == "tool_use"
            ]

            if self.verbose:
                for block in response.content:
                    if block.type == "text":
                        print(f"\n[{self.name}] Output: {block.text}")
                    elif block.type == "tool_use":
                        params_str = ", ".join(
                            [f"{k}={v}" for k, v in block.input.items()]
                        )
                        print(
                            f"\n[{self.name}] Tool call: "
                            f"{block.name}({params_str})"
                        )

            await self.history.add_message(
                "assistant", response.content, response.usage
            )

            if tool_calls:
                tool_results = await execute_tools(
                    tool_calls,
                    tool_dict,
                )
                if self.verbose:
                    for block in tool_results:
                        print(
                            f"\n[{self.name}] Tool result: "
                            f"{block.get('content')}"
                        )
                await self.history.add_message("user", tool_results)
            else:
                return response

    async def run_async(self, user_input: str) -> list[dict[str, Any]]:
        """Run agent with MCP tools asynchronously."""
        async with AsyncExitStack() as stack:
            original_tools = list(self.tools)

            try:
                mcp_tools = await setup_mcp_connections(
                    self.mcp_servers, stack
                )
                self.tools.extend(mcp_tools)
                return await self._agent_loop(user_input)
            finally:
                self.tools = original_tools

    def run(self, user_input: str) -> list[dict[str, Any]]:
        """Run agent synchronously"""
        return asyncio.run(self.run_async(user_input))
