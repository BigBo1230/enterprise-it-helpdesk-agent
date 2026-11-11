# 企业 IT 服务台智能助手

**Enterprise IT Helpdesk Agent** 是一个面向内部 IT 支持场景的多租户 AI 服务台。员工可以用自然语言查制度、排查常见故障、创建和追踪工单；Agent 使用 RAG 提供带来源的答案，并通过可靠的异步适配器连接本地模拟的 Jira、GitHub Issues 与 Slack。

这是一个可运行、可学习的 FDE 项目：重点展示 RAG、LangGraph 工具调用、外部系统集成、租户隔离、提示注入防护、离线评测和容器化运维。三种外部系统均为本地模拟器，不会向真实 Jira、GitHub 或 Slack 发请求。

## 能力概览

- FastAPI + LangGraph 对话 Agent，可查询知识、创建/查询/升级工单。
- 文档上传与 RAG 检索，回答包含来源引用；检索内容按租户隔离。
- 可切换 Jira Mock 或 GitHub Issues Mock 作为工单后端。
- Slack Mock 接收新工单与人工升级通知；通知状态与工单同步状态独立。
- Transactional outbox、幂等键、指数退避、审计日志，降低跨服务失败导致的重复或丢失。
- 租户 API key、租户级数据隔离、限流，以及 prompt injection 防护。
- React + Vite 控制台、Docker Compose、本地评测集、OpenAPI 文档和 GitHub Actions CI。

## 快速启动

1. 复制 `.env.example` 为 `.env`，配置模型供应商和一个有效的模型 API key。使用阿里百炼时：

   ```text
   AI_AGENT_MODEL_PROVIDER=bailian
   AI_AGENT_MODEL_NAME=qwen-flash
   BAILIAN_API_KEY=你的百炼通用API密钥
   AI_AGENT_BAILIAN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
   ```

2. 在项目目录启动服务：

   ```bash
   docker compose up --build -d
   ```

3. 打开 <http://localhost:5173> 使用聊天控制台，或打开 <http://localhost:8000/docs> 查看 FastAPI。

服务包括 `api`、`frontend`、`qdrant`、`ticket-worker`、`jira-mock`、`github-mock` 和 `slack-mock`。可在 `.env` 设置开发租户密钥，例如 `AI_AGENT_TENANT_API_KEYS={"demo":"replace-with-a-local-random-key"}`，然后在 Swagger 的 Authorize 中填入对应的 `x-api-key`。不要把真实密钥提交到 Git，也不要贴进聊天记录。

## 工单与通知集成

默认使用 `jira_mock`。若想让**新建**工单写入 GitHub Issues Mock，在 `.env` 中设置 `AI_AGENT_TICKET_BACKEND=github_mock`，再重建 `api` 与 `ticket-worker`。切换只影响之后创建的工单；已有待处理 outbox 事件仍按工单记录中的 provider 投递。

Jira Mock、GitHub Mock 和 Slack Mock 的 API 文档分别位于 <http://localhost:8090/docs>、<http://localhost:8091/docs> 和 <http://localhost:8092/docs>。本地租户工单和审计信息保存在 `ticket_data`；模拟外部数据使用独立 Docker volumes。

工单返回 `integration_status` 表示 Jira/GitHub 是否同步，`notification_status` 表示 Slack 通知是否送达。访问 `GET /v1/tickets/{ticket_id}` 查看单张工单，访问 `GET /v1/tickets/integration-summary` 查看当前租户 outbox 队列。聊天使用与 Swagger 相同的租户 `x-api-key`。

### 可靠性设计课程

阅读 [事务发件箱与 Jira](docs/jira-adapter-lesson.md) 和 [GitHub、Slack 适配器](docs/github-adapter-lesson.md)，理解 provider 与通知渠道的职责边界、幂等同步及失败重试。

### 防止重复创建工单

聊天与 `POST /v1/tickets` 请求都使用 `Idempotency-Key`。前端为每条用户消息生成唯一 ID 并把它作为幂等键；网络超时后，使用相同键和相同内容重试会返回原工单，使用相同键提交不同内容会得到 `409 Conflict`。幂等键按租户隔离；只有在确实要新建另一张工单时才使用新键。该机制保护写操作，不缓存模型回答。

```bash
curl -X POST http://127.0.0.1:8000/v1/tickets \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: employee-42-vpn-report-001" \
  -H "x-api-key: <tenant-api-key>" \
  -d '{"title":"VPN connection failure","description":"VPN-403 on the employee laptop.","priority":"P2","requester":"employee-42"}'
```

通过 `GET /v1/tickets/{ticket_id}/events` 查看工单操作历史。接口使用相同的租户密钥，返回操作类型、调用渠道、结果、请求 ID 和安全的状态变更，不返回工单描述、凭据和原始幂等键。事件模型与保留限制见 [审计日志](docs/audit-log.md)。

## RAG 与安全

使用 JSON 添加知识：

```bash
curl -X POST http://127.0.0.1:8000/v1/rag/documents \
  -H "Content-Type: application/json" \
  -H "x-api-key: <tenant-api-key>" \
  -d '{"source":"launch-notes","text":"Production agents need evals, tracing, auth, rate limits, and rollback plans.","metadata":{"kind":"note"}}'
```

也可以通过前端上传文件，支持 `.txt`、`.md`、`.markdown`、`.json` 和 `.pdf`。

测试检索：

```bash
curl "http://127.0.0.1:8000/v1/rag/search?q=what%20does%20production%20need" \
  -H "x-api-key: <tenant-api-key>"
```

本地开发默认连接 `http://localhost:6333` 的 Qdrant，并使用本地 FastEmbed 嵌入模型。`docker compose up --build` 会同时启动 Qdrant。仅在单进程快速实验时，可以清空 `AI_AGENT_QDRANT_URL`，改用 `.qdrant` 内嵌存储。

通过 [多租户隔离](docs/tenant-isolation.md)、[提示注入防护](docs/prompt-injection-defense.md)、[审计日志](docs/audit-log.md) 和 [RAG 评测](docs/rag-evaluation.md) 深入了解访问控制、输入边界与质量评估。离线语料和客户场景评测见 `tests/evals/` 与 [场景评测说明](docs/customer-scenario-evaluation.md)。

RAG 支持 `.txt`、`.md`、`.markdown`、`.json`、`.pdf` 上传；默认用本地 FastEmbed 嵌入模型和 Qdrant 向量库。回答会返回知识来源及检索分数。离线评测无需调用在线模型；实时模型评测会产生模型供应商费用。

## API 与运维

- OpenAPI / Swagger：<http://localhost:8000/docs>
- Health：`GET /health/live`、`GET /health/ready`
- 对话：`POST /v1/agent/invoke`、`POST /v1/agent/stream`
- 工单：`POST/GET /v1/tickets`、`GET /v1/tickets/{ticket_id}`、`POST /v1/tickets/{ticket_id}/escalate`
- 工单审计：`GET /v1/tickets/{ticket_id}/events`
- RAG：`POST /v1/rag/documents`、`POST /v1/rag/files`、`GET /v1/rag/search`

生产环境应使用 PostgreSQL 等服务端数据库、Redis 共享限流状态、外部密钥管理、持久化队列/可观测性后端，并配置备份、保留策略和回滚方案。当前 SQLite、内存限流和本地模拟服务适用于学习与单机演示，不作为多副本生产部署配置。

## 开发与评测

```bash
make lint          # ruff check
make format        # ruff format
make typecheck     # mypy strict
make test          # pytest with coverage
make eval-offline  # run the agent eval harness against the stub graph
make eval-safety   # verify tool authorization and tenant boundaries
make eval-rag      # calculate retrieval metrics against local Qdrant data
make eval          # run the eval harness against the configured live model
```

本地评测集在 `tests/evals/*.jsonl`。`make eval-offline` 用于快速离线检查；`make eval` 使用配置的在线模型，应在理解调用成本后再运行。CI 配置位于 `.github/workflows/`。

当前小型教学基线（3 条检索问题、1 条无答案问题、9 条客服安全场景）：

| 指标 | 结果 |
| --- | ---: |
| Agent 离线回归 | 3/3 通过 |
| 客服安全场景 | 9/9 通过，0 次安全失败 |
| Recall@4 | 1.000 |
| MRR | 1.000 |
| Precision@4 | 1.000 |
| 无答案拒答准确率 | 1.000 |

这些数字证明评测流程能够运行，只代表仓库内的小型人工标注样例，不代表真实企业流量上的普遍准确率。扩大语料后应重新标注测试集，并按部门、语言、权限和故障类型分层报告。

## 项目结构

```text
src/ai_agent_template/
  api/              FastAPI 路由、认证和租户上下文
  agent/            LangGraph 状态、节点、工具与服务层
  core/             配置、日志与应用生命周期
  rag/              文档处理、嵌入、Qdrant 检索
  tickets/          工单存储、provider 适配器与 outbox worker
  mock_jira/        Jira REST 本地模拟器
  mock_github/      GitHub Issues 本地模拟器
  mock_slack_app.py Slack 通知本地模拟器
frontend/           React/Vite 服务台控制台
docs/               架构、课程、安全和评测说明
tests/evals/        RAG 与客户场景 JSONL 评测集
```

更完整的设计与生产化路线见 [架构说明](docs/architecture.md)，发布前检查方法见 [GitHub 发布清单](docs/release-checklist.md)。

## License

MIT

本项目基于 [phantran/ai-agent-template](https://github.com/phantran/ai-agent-template) 改造。原项目的 MIT 许可与版权声明保留在 [LICENSE](LICENSE)。
