# 本地离线模拟示例

这个入口复用本项目的本地运行时，演示如何替换托管服务的关键流程。无需 API 密钥、外部账号、Docker 或额外 Python 包，使用 Python 3.10+ 的标准库即可运行。

## 一次运行全部场景

在仓库根目录执行：

```bash
cd managed-agents
python3 kimi.py demo
```

程序读取 `demo-fixtures/scenarios.json` 中的 11 个场景及 `demo-fixtures/product.md` 中的虚构产品资料，创建会话、检索资料、运行一次简报定时任务，并将旅行草稿传给第二个模拟角色。

输出包含数据库路径、每个场景的会话 ID、模拟回复、引用来源、调度运行记录和审阅结果。结果文件默认为 `.kimi/demo-results.json`，数据库默认为 `.kimi/demo.db`；二者均被 Git 忽略。

```bash
# 显式指定一套独立的演示文件
python3 kimi.py --db .kimi/my-demo.db demo --output .kimi/my-demo-results.json
```

每次运行会新增会话和一次性任务；已有消息保留。示例资料重复导入时由现有知识库按内容去重。

## 可以替换哪些模块？

| 原始模块或服务 | 本例的替代方式 | 实际效果 |
| --- | --- | --- |
| 托管 Agents 配置 | 11 个本地 profile | 真实读取配置；无需创建云端智能体 |
| Sessions 和消息历史 | SQLite 会话、事件表 | 真实持久化，可重启后回放 |
| 知识库和记忆存储 | 本地文件、词项索引和引用 ID | 真实导入、检索和来源记录；不做语义蒸馏 |
| 云端调度 | SQLite 一次性及间隔任务 | 真实执行本地调度，生成模拟回复 |
| Slack、Linear、Sentry、GitHub 输入 | JSON 场景和 Markdown 素材 | 模拟事件和发布记录，无 OAuth、网络请求或 webhook |
| 对外回复投递 | 本地结果 JSON | `delivery` 明确标记为模拟；没有向外部服务发送消息 |
| 旅行规划和审阅 | 草稿会话，再传给第二个角色 | 真实传递已保存内容；角色回复为固定模板，无实时地图 |
| MCP 会话工具 | 直接调用本地会话列表和历史方法 | 展示工具底层行为；未启动 MCP 传输层 |
| 模型推理 | `DemoProvider` 固定模板 | 返回输入和实际检索到的资料，标记 `OFFLINE DEMO`；没有真实推理 |
| Vault 凭据 | 演示中无需凭据 | 未模拟安全凭据存储或注入 |
| Python 沙箱 | 保存描述执行请求的会话 | 不执行代码，也不伪造执行成功；真实执行仍使用已有 Docker 后端 |

示例中的星河笔记、退款规则、收入和故障事件均为虚构素材，不是 Kimi 产品政策。模拟回复是为了展示流程，不代表实际的研究、计算、诊断或规划结果。

## 浏览器中试用

```bash
python3 kimi.py --demo --serve
```

打开 `http://127.0.0.1:8765/`，页面会显示“Offline Demo”。可以选择不同 profile，创建和回放会话，导入文档、检索、创建定时任务。Python 执行在演示模式下关闭。

如需立即查看预置资料和场景，先运行 `python3 kimi.py demo`，再启动浏览器服务。场景使用知识集合 `offline-demo`；新建会话时可在 Knowledge collection 中填写该名称。

```bash
# 单条离线消息
python3 kimi.py --demo --profile slack --message '请起草一个回复'

# 复用预置知识集合
python3 kimi.py --demo --profile knowledge-wiki \
  --knowledge-collection offline-demo --message '星河笔记退款期限是多少？'
```

离线模式默认使用独立的 `demo.db`，不读取 `KIMI_SESSIONS_DB` 来选择数据库。显式指定 `--db` 时，操作该指定文件。只向演示服务或 `--demo worker` 提交希望用模拟回复处理的任务。

## 使用真实 Kimi

省略 `--demo` 可使用原有 Kimi 模型调用：配置仓库根目录的 `.env.kimi.local` 后，从根目录运行：

```bash
./scripts/kimi-run managed-agents python3 kimi.py --serve
```

真实模式默认使用 `.kimi/sessions.db`。它继续使用本地会话、知识库和调度；模型生成需要 Kimi 凭据，Python 执行需要 Docker。

此演示新增独立入口，未将原始 React 应用、webhook 桥接或 MCP 服务器改接到本地 API。`autonomous-coding` 的 Code SDK 依赖和操作类示例的可选提供商也未由本例替换。

## 验证

```bash
python3 -m unittest tests.test_demo_provider tests.test_demo_workflows -v
python3 -m unittest discover -s tests -v
```

新增测试覆盖无凭据及无网络/子进程条件下的全部场景、知识来源、重启后会话回放、调度、独立数据库默认值和 HTTP 演示模式。
