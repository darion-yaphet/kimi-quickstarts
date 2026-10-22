# Kimi Quickstarts

Runnable examples for building agents and applications with Kimi. This fork
adapts [Anthropic's Claude Quickstarts](https://github.com/anthropics/claude-quickstarts),
preserves its Git history and MIT license, and uses Kimi's Anthropic-compatible
Messages endpoint for model calls. The Anthropic SDK remains a protocol client;
it does not require an Anthropic API key when configured for Kimi.

The core examples have Kimi execution paths. The Managed Agents migration is
in progress: a local session service is available, while its original product
UIs, external connectors, and hosted cloud features still need adapters.

## Configure once

Create a local configuration from the example and set your own key:

```bash
cp .env.kimi.example .env.kimi.local
# Edit .env.kimi.local; do not commit credentials.
```

Run a quickstart with the shared environment launcher:

```bash
./scripts/kimi-run customer-support-agent npm ci
./scripts/kimi-run customer-support-agent npm run dev
```

The launcher sets `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `ANTHROPIC_MODEL`
and `MODEL` from the Kimi configuration for SDK and CLI compatibility. Individual
examples document additional dependencies and services in their READMEs.

Kimi Code's domestic Anthropic base URL is `https://api.kimi.com/coding/`;
the overseas equivalent is `https://api.kimi.ai/coding/`. The default model is
`kimi-for-coding`. Check [Kimi's current model documentation](https://www.kimi.com/code/docs/en/kimi-code/models)
for model access and image capabilities. Keep the client's real identity:
this fork does not impersonate another application's User-Agent.

Kimi Code keys are for programming use. For product or enterprise deployments,
consult the [Kimi platform comparison](https://www.kimi.com/code/docs/en/#platform-comparison)
and configure the appropriate service and credentials.

## Examples

| Directory | Purpose |
| --- | --- |
| [customer-support-agent](customer-support-agent/) | Customer support chat; optional AWS knowledge retrieval |
| [financial-data-analyst](financial-data-analyst/) | Financial analysis and custom chart tools |
| [agents](agents/) | Small reusable agent and MCP tool loop |
| [autonomous-coding](autonomous-coding/) | Long-running coding sessions |
| [browser-use-demo](browser-use-demo/) | Browser automation using local Playwright tools |
| [computer-use-demo](computer-use-demo/) | Docker desktop controlled through custom tools |
| [computer-use-best-practices](computer-use-best-practices/) | Native macOS desktop agent |
| [browser-toolset](browser-toolset/) | Minimal CDP browser driver and ordinary tool adapter |
| [computer-toolset](computer-toolset/) | Minimal VNC desktop driver and ordinary tool adapter |
| [managed-agents](managed-agents/) | Local Kimi session service; original cloud integrations remain pending |

Migration and validation details are recorded in [MIGRATION.md](MIGRATION.md).
Renaming a project or changing an endpoint does not implement Anthropic-hosted
Agents, Sessions, Vaults, or scheduling APIs. The status document distinguishes
working Kimi paths from remaining hosted-service dependencies.

## Repository remotes

```text
origin    https://github.com/darion-yaphet/kimi-quickstarts.git
upstream  https://github.com/anthropics/claude-quickstarts.git
```

The original overview is retained in [docs/UPSTREAM_README.md](docs/UPSTREAM_README.md).
See [LICENSE](LICENSE) for the upstream MIT license.
