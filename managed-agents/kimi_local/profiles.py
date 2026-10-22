"""Honest capability profiles for the original Managed Agents examples."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Profile:
    name: str
    purpose: str
    instructions: str
    integration_status: str
    remaining_integrations: tuple[str, ...]

    def public_dict(self) -> dict:
        result = asdict(self)
        result["remaining_integrations"] = list(self.remaining_integrations)
        result["local_capabilities"] = [
            "SQLite-backed sessions and event history",
            "Kimi chat through the Anthropic-compatible Messages API",
            "loopback HTTP API and interactive CLI",
            "Document ingestion, lexical retrieval and provenance citations",
            "Persistent one-shot and interval scheduling with run history",
            "Isolated Python jobs when Docker and the configured image are available",
        ]
        result["hosted_capabilities"] = {
            "agents": False,
            "sessions": False,
            "vaults": False,
            "dreams": False,
            "cron": False,
            "oauth": False,
            "sandbox_tools": False,
        }
        return result


def _profile(
    name: str,
    purpose: str,
    instructions: str,
    integration_status: str,
    *remaining: str,
) -> Profile:
    return Profile(name, purpose, instructions, integration_status, remaining)


PROFILES = {
    profile.name: profile
    for profile in (
        _profile(
            "assistant-ui",
            "Spreadsheet and data-analysis chat",
            "Help analyze user-provided data. Explain calculations and uncertainty clearly.",
            "Local chat is ready; the existing assistant-ui frontend is not wired to this API.",
            "Replace its Managed Sessions adapter with the local HTTP API",
            "Implement file upload and chart tool rendering",
            "Add user authentication before remote exposure",
        ),
        _profile(
            "chat-sdk",
            "Research-oriented chat",
            "Answer from conversation context. State when current external research would be required.",
            "Local chat is ready; the existing Chat SDK bridge still targets Managed Sessions.",
            "Replace the Chat SDK Managed Sessions bridge",
            "Provide a reviewed web-search tool if external research is required",
            "Implement streaming if the UI requires token deltas",
        ),
        _profile(
            "copilot-kit-ag-ui",
            "Personal finance explanation and planning",
            "Give educational financial analysis with explicit assumptions. Do not execute transactions.",
            "Local chat is ready; the AG-UI Managed Agents adapter is not connected.",
            "Build an AG-UI adapter for the local session endpoints",
            "Reimplement client chart actions",
        ),
        _profile(
            "daily-brief",
            "Draft a concise daily brief from supplied material",
            "Summarize only material supplied in the conversation. Mark missing sources.",
            "Scheduled local brief generation with knowledge retrieval is ready; external collection and delivery are not connected.",
            "Add reviewed GitHub and Slack read connectors",
            "Add an opt-in Slack delivery action",
        ),
        _profile(
            "knowledge-wiki",
            "Answer questions from locally ingested documents",
            "Use retrieved evidence, cite the supplied citation IDs, and say when evidence is insufficient.",
            "Local text/PDF ingestion, lexical retrieval and source citations are ready; hosted dreams are not used.",
            "Optional semantic wiki synthesis and cross-document conflict resolution",
        ),
        _profile(
            "linear",
            "Draft responses and issue analysis for Linear",
            "Draft issue responses from supplied context. Never claim to have changed an issue.",
            "Local drafting is ready; Linear OAuth, webhooks, and comment posting are not connected.",
            "Implement Linear OAuth and webhook verification",
            "Persist workspace-to-session routing",
            "Add an explicit comment-posting action",
        ),
        _profile(
            "mcp-server-typescript",
            "General local session assistant",
            "Be a concise assistant and use only information present in the conversation.",
            "The local session API is ready; the existing MCP server still wraps Anthropic Sessions.",
            "Replace its nine Managed Sessions tools with local API tools",
            "Define authentication and profile allowlists for remote use",
        ),
        _profile(
            "roadtrip-planner",
            "Draft road-trip plans from user-provided constraints",
            "Plan practical road trips. Flag facts that require live map, weather, or booking data.",
            "Local planning chat is ready; maps, vault injection, and multiagent review are absent.",
            "Connect the existing UI to the local session API",
            "Add reviewed maps and places connectors",
            "Reimplement reviewer workflow if needed",
        ),
        _profile(
            "self-hosted-sandboxes",
            "Execute Python in isolated local Docker jobs",
            "Use the run_python tool when available. Report its job ID, status and output accurately; do not claim to provision cloud infrastructure.",
            "Persisted Python jobs, Docker isolation, resource limits and run audit are implemented; Docker and a prepared image are required.",
            "Adapters for the original cloud sandbox providers",
            "Remote worker authentication and multi-host deployment",
        ),
        _profile(
            "sentry",
            "Draft Sentry issue triage from supplied event data",
            "Rank supplied errors by user impact and explain likely causes without claiming Sentry access.",
            "Local triage and scheduled runs are ready; Sentry OAuth/MCP and Seer are not connected.",
            "Implement Sentry authentication and read connector",
            "Add Seer integration separately if licensed",
        ),
        _profile(
            "slack",
            "Draft helpful Slack replies",
            "Draft a reply from supplied thread context. Never claim to have posted it.",
            "Local drafting is ready; Slack events, OAuth, routing, and posting are not connected.",
            "Implement Slack OAuth and request-signature verification",
            "Persist channel/thread routing",
            "Add an explicit reply-posting action",
        ),
    )
}


def get_profile(name: str) -> Profile:
    if not isinstance(name, str):
        raise ValueError("Profile name must be a string")
    try:
        return PROFILES[name]
    except KeyError as error:
        choices = ", ".join(sorted(PROFILES))
        raise ValueError(
            f"Unknown profile {name!r}. Choose one of: {choices}"
        ) from error
