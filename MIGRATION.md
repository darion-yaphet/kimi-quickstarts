# Kimi migration status

This fork preserves the upstream history and MIT attribution. Migration is
ongoing; a local prompt profile is not equivalent to a migrated product
integration. The original repository remains independently available alongside
this clone.

## Execution paths

| Directory | Kimi implementation | Validation / remaining limits |
| --- | --- | --- |
| `customer-support-agent` | Configurable Kimi Messages client, Kimi model UI, full JSON replies without Claude assistant prefill, optional Bedrock retrieval | Next.js build passed; real `/api/chat` call returned HTTP 200 and validated response fields. Bedrock needs separate AWS setup. |
| `financial-data-analyst` | Kimi Messages and ordinary chart tools; Kimi model picker | Next.js build and lint passed. |
| `agents` | Kimi Messages defaults, optional caching off, ordinary tools | Configuration and tool tests pass. Code execution requires a local Docker runtime/image; web search requires an explicit backend. |
| `autonomous-coding` | Claude Agent SDK forwarded to Kimi with endpoint and all task-tier models configured | Configuration and 89 security checks pass. A real Claude CLI request routed through Kimi returned `OK`; a full application-building run has not been performed. |
| `browser-use-demo` | Kimi provider, custom Playwright tools, configured element-finder client | Targeted sampling and UI tests pass. Full browser integration needs a working Chromium environment. |
| `computer-use-demo` | Kimi provider, custom computer/bash/editor schemas; Kimi-specific key storage | Desktop unit/UI tests, Ruff and Pyright pass. A real Kimi tool/screenshot conversation passed with a simulated screenshot. Actual Linux desktop requires Docker/X11/VNC. |
| `computer-use-best-practices` | Kimi provider with explicit local tools; Claude hosted computer/advisor/compaction/effort omitted | 124 tests passed; Ruff and Pyright passed. Native desktop operation requires macOS permissions and an isolated test environment. |
| `browser-toolset` | Python/TypeScript custom tools backed by CDP driver; portable browser-state results | SDK policy/normalization and truncation tests pass; TypeScript strict checks pass. Python and TypeScript browser exercises passed against Chromium, and a real Kimi + Chromium navigate/read/click/read loop passed. |
| `computer-toolset` | Python/TypeScript custom tools backed by VNC driver; confirmation hooks retained | Mock loop/truncation tests and TypeScript strict checks pass. No live VNC desktop is available here. |
| `managed-agents` | New local Kimi session runtime with SQLite, browser UI, CLI/HTTP and eleven profiles | 13 runtime/provider/HTTP tests and a real Kimi chat with persistence pass. Browser UI create/chat/reload/safe-text rendering passed. Original product interfaces and cloud integrations are **not fully migrated**. |

## Shared configuration

Use `.env.kimi.example` and `scripts/kimi-run` to load a single ignored local
configuration. The launcher explicitly selects Kimi for desktop/browser
providers and maps model/key/base URL values into the Anthropic-compatible
SDK and Claude Code environment variables. No client identity is forged.

Real API keys, local databases, downloaded dependencies and generated runtime
state are excluded from Git. The Anthropic SDK is retained as an API protocol
client and for local tool-driver interfaces, not as a requirement to purchase
Anthropic model access.

## Managed Agents: outstanding work

See [managed-agents/KIMI_MIGRATION.md](managed-agents/KIMI_MIGRATION.md) for
the per-profile capability matrix and implemented local endpoints.

The original `assistant-ui`, Chat SDK, CopilotKit/AG-UI and road-trip interfaces
still need local session adapters and streaming/tool-rendering support. Slack,
Linear and Sentry integrations still need their connectors and request/OAuth
handling. Knowledge wiki needs document ingestion and provenance storage.
Unattended briefs need scheduling and explicit delivery. Self-hosted sandboxes
need a work queue and isolated executors. Kimi does not provide Anthropic's
hosted Agents, Sessions, Vaults or Dreams APIs by changing a base URL.

The local service supplies a usable session/persistence foundation for that
work; it does not silently redirect any original hosted-service client to
Kimi or claim that those integrations are complete.
