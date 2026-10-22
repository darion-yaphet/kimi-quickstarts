# Kimi 快速入门示例

可运行的示例，帮助你使用 Kimi 构建智能体和应用。本分支改编自
[Anthropic 的 Claude Quickstarts](https://github.com/anthropics/claude-quickstarts)，
保留了原有 Git 历史和 MIT 许可证，并使用 Kimi 兼容 Anthropic 的 Messages 端点调用模型。
Anthropic SDK 在此作为协议客户端；配置为使用 Kimi 时，无需 Anthropic API 密钥。

核心示例已提供 Kimi 执行路径。Managed Agents 的迁移仍在进行中：本地会话、文档摄取与检索、
任务调度以及 Docker Python 执行已可用，但原有产品界面和外部连接器仍需适配。

## 一次配置

根据示例创建本地配置，并设置自己的密钥：

```bash
cp .env.kimi.example .env.kimi.local
# 编辑 .env.kimi.local；不要提交凭据。
```

使用共享环境启动器运行快速入门示例：

```bash
./scripts/kimi-run customer-support-agent npm ci
./scripts/kimi-run customer-support-agent npm run dev
```

启动器从 Kimi 配置中设置 `ANTHROPIC_API_KEY`、`ANTHROPIC_BASE_URL`、`ANTHROPIC_MODEL`
和 `MODEL`，以兼容 SDK 和命令行工具。各示例的 README 说明了额外的依赖和服务。

Kimi Code 国内 Anthropic 基础 URL 为 `https://api.kimi.com/coding/`；海外对应地址为
`https://api.kimi.ai/coding/`。默认模型为 `kimi-for-coding`。模型访问权限和图像能力请参阅
[Kimi 最新模型文档](https://www.kimi.com/code/docs/en/kimi-code/models)。请保持客户端的真实身份：
本分支不会通过 User-Agent 冒充其他应用。

Kimi Code 密钥用于编程用途。对于产品或企业部署，请参阅
[Kimi 平台对比](https://www.kimi.com/code/docs/en/#platform-comparison)，并配置合适的服务和凭据。

## 示例

| 目录 | 用途 |
| --- | --- |
| [customer-support-agent](customer-support-agent/) | 客户支持聊天；可选 AWS 知识检索 |
| [financial-data-analyst](financial-data-analyst/) | 财务分析和自定义图表工具 |
| [agents](agents/) | 小型可复用智能体及 MCP 工具调用循环 |
| [autonomous-coding](autonomous-coding/) | 长时间运行的编程会话 |
| [browser-use-demo](browser-use-demo/) | 使用本地 Playwright 工具实现浏览器自动化 |
| [computer-use-demo](computer-use-demo/) | 通过自定义工具控制的 Docker 桌面 |
| [computer-use-best-practices](computer-use-best-practices/) | 原生 macOS 桌面智能体 |
| [browser-toolset](browser-toolset/) | 精简的 CDP 浏览器驱动和普通工具适配器 |
| [computer-toolset](computer-toolset/) | 精简的 VNC 桌面驱动和普通工具适配器 |
| [managed-agents](managed-agents/) | 本地 Kimi 会话服务；原有云集成尚待迁移 |

迁移和验证详情记录在 [MIGRATION.md](MIGRATION.md) 中。重命名项目或更改端点并不会实现
Anthropic 托管的 Agents、Sessions、Vaults 或调度 API。状态文档区分了可用的 Kimi 执行路径与
仍依赖托管服务的功能。

## 仓库远程地址

```text
origin    https://github.com/darion-yaphet/kimi-quickstarts.git
upstream  https://github.com/anthropics/claude-quickstarts.git
```

原始概览保留在 [docs/UPSTREAM_README.md](docs/UPSTREAM_README.md) 中。
上游 MIT 许可证请参阅 [LICENSE](LICENSE)。
