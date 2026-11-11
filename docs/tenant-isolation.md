# 多租户隔离：本地演示

## 身份与数据

`AI_AGENT_TENANT_API_KEYS` 是 `.env` 中的 JSON 对象，格式为 `{"demo":"随机密钥A","beta":"随机密钥B"}`。服务端通过 `x-api-key` 找到租户；请求体里的租户字段不会决定访问范围。

旧工单与旧知识库文档归属 `demo`。新工单写入 `tenant_id`；新文档写入带租户标识的 Qdrant 元数据。工单列表、单张查询、升级、聊天工具、知识库检索均按已认证租户过滤。不同租户即使使用相同聊天 thread ID，也不会共用 LangGraph 会话。

## 手动验证

1. 从项目根目录 `.env` 读取 `AI_AGENT_TENANT_API_KEYS`，分别保存 demo、beta 的密钥。不要把它们提交到 Git。
2. 打开 `http://localhost:8000/docs`，点击右上角 Authorize，在 `x-api-key` 输入 demo 密钥。运行 `GET /v1/tickets`，能看到之前的演示工单。
3. 改用 beta 密钥，再运行 `GET /v1/tickets`，初始应是空数组。使用 demo 工单编号运行 `GET /v1/tickets/{ticket_id}` 和 `POST /v1/tickets/{ticket_id}/escalate`，均应返回 404。
4. 用 beta 密钥创建一张测试工单；切回 demo 后，此工单不应出现在列表中。
5. 在网页顶部的 `Tenant API key` 输入框粘贴所用租户密钥，再使用聊天和知识库功能。切换密钥时网页会清除屏幕上旧租户的聊天内容。

本地演示使用静态 API key。正式企业应用还需要接入身份提供商，并管理用户、角色、密钥轮换与审计。

## 工单创建的重试保护

`POST /v1/tickets`、`POST /v1/agent/invoke` 和 `POST /v1/agent/stream` 需要
`Idempotency-Key` 请求头（1–128 个字符）。聊天页面为每条用户消息生成一个稳定 ID，并把它作为键传给 API；
聊天流中断后，用户可以点消息下方的安全重试按钮，复用同一键。`x-request-id` 用于追踪单次 HTTP 请求，
重试时可以变化；`Idempotency-Key` 标识同一业务操作，重试时必须保持不变。工单存储按 `(tenant_id, idempotency_key)` 建唯一记录，
同键同内容重放会返回第一次创建的工单，同键不同内容返回 409。不同租户可各自使用相同键。

这项机制只避免重复执行工单创建副作用，不缓存模型回答。直接调用建单 API 时，也要自行生成请求键；
真正开始一张新工单时请使用新键。
