# Kimi local runtime

`kimi.py` is a local Kimi execution service. It replaces hosted conversation
state with SQLite, indexes documents for evidence-based answers, executes
persistent schedules and runs isolated Python jobs in Docker. It calls Kimi
through the Anthropic-compatible Messages API.
It is deliberately a separate entrypoint: it does not imitate endpoints that
Kimi does not provide.

## What works

- Persistent local sessions with `idle`, `running`, and `error` state
- Append-only user, assistant, and error events
- Conversation replay after process restart
- Eleven purpose-specific profiles
- Interactive CLI, one-shot CLI, and a JSON HTTP API
- Dependency-free browser UI at `/` for profiles, sessions, history, and chat
- Serialized turns within one process for each session
- Kimi endpoint, model, output length, and database configuration through env
- Document ingestion, a term index, versioned citation IDs and chat retrieval
- Persisted one-shot/interval schedules, leases, retry and run/session audit
- Resource-limited Docker jobs, bounded output, verified cleanup and recovery

See [LOCAL_RUNTIME.md](LOCAL_RUNTIME.md) for CLI commands, all service endpoints,
operational constraints and integration tests.

## HTTP API

Start it with `python kimi.py --serve`. The supported routes are:

| Method | Path | Behavior |
| --- | --- | --- |
| `GET` | `/` | Same-origin local browser UI |
| `GET` | `/health` | Local process health |
| `GET` | `/v1/profiles` | Capability status for every profile |
| `GET` | `/v1/sessions?profile=slack` | List persisted sessions |
| `POST` | `/v1/sessions` | Create a session from `profile` and optional `title` |
| `GET` | `/v1/sessions/{id}` | Retrieve local session metadata |
| `GET` | `/v1/sessions/{id}/events` | Replay its event history |
| `POST` | `/v1/sessions/{id}/messages` | Send `{ "content": "..." }` to Kimi |

The server accepts bodies up to 1 MB; a message is limited to 100,000
characters. POST requests require `application/json`. On the default loopback
bind, requests with a non-loopback `Host` or foreign `Origin` are rejected to
reduce DNS-rebinding and browser request-forgery risk. It has no remote
authentication, rate limiter, or TLS, so loopback remains the supported
default. Non-loopback binding is rejected; the unauthenticated `--allow-remote`
escape hatch has been removed now that the service exposes execution and
scheduling controls.

## Profile migration status

| Profile | Local capability | Still required for the original experience |
| --- | --- | --- |
| `assistant-ui` | Spreadsheet/data conversation | UI adapter, uploads, chart actions, authentication |
| `chat-sdk` | Research-style conversation | Chat SDK adapter, reviewed search, streaming |
| `copilot-kit-ag-ui` | Finance conversation | AG-UI adapter and interactive chart actions |
| `daily-brief` | Scheduled brief generation with local knowledge | GitHub/Slack readers, opt-in delivery |
| `knowledge-wiki` | Local ingestion, indexed retrieval and source citations | Optional semantic wiki synthesis/conflict resolution |
| `linear` | Issue-response drafting | OAuth, signed webhooks, thread routing, explicit posting |
| `mcp-server-typescript` | General session service | Local MCP tool implementation and auth/allowlists |
| `roadtrip-planner` | Constraint-based planning | UI adapter, maps/places data, reviewer workflow |
| `self-hosted-sandboxes` | Persisted Python jobs, Docker execution, limits and audit | Original multi-cloud provider adapters and remote worker authentication |
| `sentry` | Scheduled triage from supplied events | Sentry auth/read connector, optional Seer |
| `slack` | Thread-reply drafting | OAuth, signed events, thread routing, explicit posting |

Run `python kimi.py --profile <name> --status` for the same information in
machine-readable JSON. Each system prompt tells the model which capabilities
are absent so a response does not claim that an external action occurred.

## Configuration

Copy values from `.env.example` into your environment. The China Kimi Code
endpoint is `https://api.kimi.com/coding/`; the overseas endpoint is
`https://api.kimi.ai/coding/`. These endpoints require Kimi Code keys. They are
different from Moonshot Open Platform endpoints and keys.

The local runtime does not read `.env` files itself. This avoids adding a config
dependency and keeps secret loading under the caller's control.

`ANTHROPIC_TIMEOUT_SECONDS` defaults to 120 seconds and
`ANTHROPIC_MAX_TOKENS` defaults to 4096. A response stopped at `max_tokens` is
recorded as an error event and returned as a provider error; partial model text
is not stored as a successful assistant reply.
