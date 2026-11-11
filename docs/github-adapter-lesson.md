# 连接 GitHub Issues 模拟服务

## 这一课学什么

现在 Jira Mock 和 GitHub Issues Mock 都遵守同一个 `TicketProvider` 协议。Agent 与 API 仍然只依赖 `TicketBackend`；配置选择 provider 后，适配层负责把通用工单字段转换成 Jira issue 或 GitHub issue。

GitHub Issues 不是另一个本地数据库入口。应用仍以自己的租户工单记录和 outbox 为准，再把事件可靠地交给所选 provider。worker 根据工单保存的 `external_system` 选择 provider，因此切换默认配置时，已有 Jira 工单的后续升级也仍会送往 Jira；已有纯本地工单仍能本地升级。

## 本地服务

- GitHub Issues Mock API 文档：<http://localhost:8091/docs>
- Jira Mock API 文档：<http://localhost:8090/docs>
- GitHub key 格式：`demo-org/it-helpdesk#<number>`
- 创建 GitHub issue 时会映射标题、描述、优先级、请求人；请求头包括当前租户和幂等键。
- 升级人工会给远端 issue 加上 `human-support` 和 `escalated` 标签。GitHub issue 仍保持 `open`，本地服务台状态记录为 `escalated`。

两个模拟 API 都使用本地 SQLite 卷和本地开发 token；不会调用 Atlassian 或 GitHub 的公网 API。`AI_AGENT_GITHUB_API_TOKEN` 是模拟服务内部凭据，不是 GitHub Personal Access Token。

## 切换到 GitHub Mock

1. 在项目 `.env` 中设置 `AI_AGENT_TICKET_BACKEND=github_mock`。
2. 在项目目录运行 `docker compose up -d --build api ticket-worker`。
3. 从聊天创建一张新工单，拿到本地 `IT-...` ID。
4. 使用 beta 的 `x-api-key` 在 `http://localhost:8000/docs` 查询工单，等待 `integration_status` 变成 `synced`，并查看 `external_system=github-mock` 与 `external_key=demo-org/it-helpdesk#...`。
5. 在 `http://localhost:8091/docs` 用本地模拟 token 和 `X-Tenant-ID: beta` 调用对应 issue 的 GET 接口查看远端副本。

API 和 worker 共用项目 `.env`。Swagger 中用于 Agent API 的 `x-api-key` 与 GitHub Mock 使用的 Bearer token 是两种不同凭据；都不要发送到聊天框或提交进 Git。

## Slack 是另一个边界

GitHub Issues 是工单系统的一个可选后端；Slack Mock 是通知渠道，不保存服务台的权威工单状态。部署后新创建的工单会把精简通知可靠地投递到 Slack Mock；人工升级只有在工单后端确认成功后才产生升级通知。部署前的历史工单不会补发通知。两类事件都有独立幂等键与重试状态，Slack 失败不会把已经创建成功的 Jira/GitHub 工单改成失败。

- Slack Mock API 文档：<http://localhost:8092/docs>
- `GET /api/notifications` 使用 `Authorization: Bearer <本地模拟 token>` 和 `X-Tenant-ID` 查看本租户通知。
- 工单的 `notification_status` 与 `integration_status` 分开；摘要也提供独立的通知计数。`notification_status` 汇总该工单所有 Slack 事件：存在失败则为 `failed`，否则存在待处理则为 `pending`，全部送达后为 `synced`。
- 通知正文只带工单 ID、标题、优先级、状态和外部引用，不附带完整问题描述。
- `AI_AGENT_SLACK_NOTIFICATIONS_ENABLED=false` 会停止新通知入队；已经入队的通知仍由 worker 重试。本地模拟器不连接真实 Slack Workspace。

## 思考

`TicketProvider` 统一了创建和升级操作，但各 provider 的字段和状态语义不同。为什么把 `external_system` 与 `external_key` 同时保存在本地工单里，比只保存一个外部 ID 更适合支持 Jira 和 GitHub？为什么 Slack 通知应该有独立状态，而不是挤进工单同步状态？
