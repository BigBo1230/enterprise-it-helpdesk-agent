# 下一课：可靠工单同步与事务发件箱

## 这一课要学什么

Agent 不应该知道工单最终存在哪里。它只使用 `TicketBackend` 约定的创建、查询、列出和升级方法。`TicketBackendAdapter` 持有本地工单存储、可选的工单 provider 和通知配置；选择 `jira_mock` 时，它会把外部写操作排入 transactional outbox，由独立 worker 通过 HTTP 同步到本地 Jira REST 模拟服务。

这个边界叫适配器模式：对上层提供稳定接口，对下层封装不同系统的 API 和字段格式。

## 一张请求如何流动

```text
聊天 / FastAPI
      │ TicketBackend
      ▼
TicketBackendAdapter ──► 本地工单 SQLite（同一事务写入工单和 outbox）
                              │
                              ▼
                         ticket-worker ── HTTP + tenant + 幂等键
      ▼
Jira Mock :8090 ───────► 独立 SQLite（模拟 Jira issue）
```

创建时，API 在一个本地数据库事务中同时写入工单和一条 outbox 事件。事务提交后 API 就能返回工单，不必等待 Jira 响应。独立 worker 领取事件并调用模拟 Jira。工单暂时会显示 `integration_status=pending`，同步成功后显示 `external_system=jira-mock`、`external_key=HELP-...` 和 `integration_status=synced`。

worker 用稳定的 outbox 幂等键调用 Jira Mock。如果远端已创建 issue，但响应丢失，重试仍会取回同一个 issue。网络失败时事件按指数退避重试，最多尝试 8 次；用同一个聊天请求安全重试时，也可以重新排入最终失败的创建事件。成功或失败都会写入不含密钥和原始请求内容的审计记录。

升级人工也走 outbox。远端已确认之前，本地状态仍是 `open`、集成状态是 `pending`；worker 成功后才把本地状态改为 `escalated` 和 `human-support`。如果 issue 还未同步，系统会先要求等待同步完成，再发起升级，避免升级事件跑到创建事件之前。

## 如何观察

- Agent API 文档：<http://localhost:8000/docs>
- 模拟 Jira 的 REST 文档：<http://localhost:8090/docs>
- Compose 服务名：`api`、`ticket-worker`、`jira-mock`、`github-mock`、`slack-mock`、`qdrant`、`frontend`
- 开发租户和模拟凭据由 Compose 环境变量提供。这个凭据只用于本地模拟器，不是 Atlassian Jira API token。
- `GET /v1/tickets` 仍读取本地工单列表；每张工单会返回 `external_key` 和 `integration_status`。刚创建时 `pending` 是正常现象，稍后查询可看到同步结果。
- `GET /v1/tickets/integration-summary` 返回当前租户的 `pending`、`retrying`、`in_progress`、`failed`、`succeeded` 数量和最老待处理时间。`succeeded` 统计已投递的 outbox 事件，升级成功也会增加计数；本次迁移前已经同步成功的工单会登记为历史成功事件。它只汇总当前 API key 所属租户，不泄露其他租户的数据。
- 同一摘要还分别统计 `notification_pending`、`notification_failed`、`notification_succeeded`。Slack 通知状态出现在工单的 `notification_status`，与 Jira/GitHub 的 `integration_status` 分开，避免“Slack 暂时不可用”误报成“工单没创建成功”。
- `GET /v1/tickets/{ticket_id}/events` 可查看 `ticket.created`、`integration.sync.succeeded`、`integration.sync.retry_scheduled`、`integration.sync.failed`、`ticket.escalation.requested` 和 `ticket.escalated` 等审计事件。审计详情不记录 API token、完整描述或原始网络错误。
- PowerShell/终端可用 `docker compose logs -f ticket-worker` 观察 worker 领取、完成和重试 outbox 事件。

## 重要的生产化边界

Transactional outbox 解决了“本地工单已提交，但 HTTP 请求没有发出去”造成的丢失窗口；它不能让本地 SQLite 和远端 Jira 变成一个分布式事务。生产环境还要监控积压、重试次数和最终失败，并对重复投递保持幂等。本课程用 Docker named volume 让 API 与 worker 共享本地 SQLite；多机部署时应改为 PostgreSQL 等服务器数据库，避免多个容器依赖共享文件数据库。

## 动手练习

1. 在 `backend.py` 找到 `TicketBackend` 协议，再对照 `TicketStore` 与 `JiraMockBackend` 的实现。
2. 打开模拟器的 `/docs`，查看 issue 创建请求里的租户、幂等键和字段映射。
3. 在 Agent API Swagger 中使用 beta 租户的 `x-api-key` 授权，调用 `GET /v1/tickets/integration-summary`，记录当前队列数字。
4. 从聊天创建一张新工单，记下本地 ID。马上查询工单和摘要：worker 可能仍在处理，状态会短暂显示 `pending` 或 `in_progress`；若已经很快变成 `synced`，也属于正常结果。
5. 再次查询工单，确认出现 `external_key` 和 `integration_status=synced`；同时确认 `notification_status=synced`。对照摘要计数、审计事件或 worker 日志看两种交付过程。
6. 用 `external_key` 在模拟器的 GET issue 接口查询远端状态，再回到工单审计事件观察同步过程。

思考：如果 API 在写入本地工单后、发送 HTTP 请求前就崩溃，为什么 outbox 可以在重启后继续处理？如果远端已创建 issue、但 worker 在确认本地成功前崩溃，稳定的远端幂等键如何避免重复 issue？

可选故障演练：在项目目录的 PowerShell 里运行 `docker compose stop jira-mock`，创建一张练习工单并确认它仍有本地 ID、集成状态为 `pending`；Slack 模拟通知仍可单独送达。再运行 `docker compose start jira-mock`，观察 worker 日志和工单状态最终转为 `synced`。练习期间不要运行 `docker compose down -v`，那会删除练习数据卷。
