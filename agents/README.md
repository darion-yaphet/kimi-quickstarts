# Kimi Agents

A minimal educational implementation of LLM agents using Kimi Code's
Anthropic-compatible API.

> **Note:** This is NOT an SDK, but a reference implementation of key concepts

## Overview & Core Components

This repo adapts Anthropic's [build effective agents](https://www.anthropic.com/engineering/building-effective-agents) patterns to Kimi Code. It shows how sophisticated AI behaviors can emerge from a simple foundation: LLMs using tools in a loop. This implementation is not prescriptive - the core logic is <300 lines of code and deliberately lacks production features.

It contains three components:

- `agent.py`: Manages Anthropic-compatible API interactions and tool execution
- `tools/`: Tool implementations (both native and MCP tools)
- `utils/`: Utilities for message history and MCP server connections

## Usage

```python
from agents.agent import Agent
from agents.tools.think import ThinkTool

# Create an agent with both local tools and MCP server tools
agent = Agent(
    name="MyAgent",
    system="You are a helpful assistant.",
    tools=[ThinkTool()],  # Local tools
    mcp_servers=[
        {
            "type": "stdio",
            "command": "python",
            "args": ["-m", "mcp_server"],
        },
    ]
)

# Run the agent
response = agent.run("What should I consider when buying a new laptop?")
```

From this foundation, you can add domain-specific tools, optimize performance, or implement custom response handling. We remain deliberately unopinionated - this backbone simply gets you started with fundamentals.

## Requirements

- Python 3.10+
- Kimi Code API key (set as `ANTHROPIC_API_KEY`)
- `anthropic` Python library, 1.0 or later
- `mcp` Python library

Copy `.env.example` values into your shell or preferred environment loader:

```bash
export ANTHROPIC_API_KEY='your-kimi-code-api-key'
export ANTHROPIC_BASE_URL='https://api.kimi.com/coding/'
export ANTHROPIC_MODEL='kimi-for-coding'
export ANTHROPIC_CONTEXT_WINDOW_TOKENS='262144'
```

The overseas endpoint is `https://api.kimi.ai/coding/`. You can select another
Kimi Code model, such as `k3-256k`, through `ANTHROPIC_MODEL` or by passing a
`ModelConfig`. Provider-specific beta/server tools are not portable; use the
local and MCP tools in this example when running against Kimi.
Prompt caching is disabled by default for compatibility and can be enabled
explicitly with `ModelConfig(enable_prompt_caching=True)` when the selected
endpoint supports Anthropic cache-control blocks.

Each user request permits at most 20 tool-execution turns by default. Set
`ModelConfig(max_tool_turns=5)` to choose another positive limit. After the
budget is used, the agent requests a final answer with tools disabled; a model
that requests another tool receives an error without executing that tool.

## Portable code execution and web search

`CodeExecutionServerTool` and `WebSearchServerTool` keep their historical
class names, but now emit ordinary custom-tool schemas that work with Kimi.
They no longer declare Anthropic-hosted server tool types.

Code execution requires Docker and runs Python in a container with no network,
a read-only root filesystem, dropped capabilities, a non-root user, and CPU,
memory, process, time, source-size, and output limits. Pull the image once
before starting the agent:

```bash
docker pull python:3.12-alpine
```

Override it with `CODE_EXECUTION_DOCKER_IMAGE`. The tool uses
`--pull=never`, so an agent call cannot fetch an image or silently change the
runtime.

Web search requires an explicit backend. Pass a sync or async callable:

```python
from agents.tools.web_search import WebSearchServerTool

def search_backend(**request):
    # Call your chosen search provider here and return result dictionaries.
    return [{"title": "Example", "url": "https://example.com", "snippet": "..."}]

search = WebSearchServerTool(backend=search_backend, max_uses=5)
```

Alternatively, set `WEB_SEARCH_BACKEND_URL` to an HTTP(S) endpoint accepting a
JSON POST and returning either a result list or `{"results": [...]}`. Set
`WEB_SEARCH_API_KEY` when that endpoint accepts a bearer token. Domain filters
are enforced again on returned result URLs by the local tool.
