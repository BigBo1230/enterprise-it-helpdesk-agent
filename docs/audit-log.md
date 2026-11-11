# 工单审计记录

## 为什么要审计

客服需要能回答“工单是谁创建或升级的、系统是否真的执行了、这次是不是重试”。模型回复只能说明模型声称了什么，不能代替后端操作记录。

## 本地实现

`ticket_audit_events` 与工单数据保存在同一个 SQLite 数据库。建单、同键重放、幂等键冲突和升级事件与对应的工单写入放在同一数据库事务中，避免工单已经改变但审计记录没有写入。

`GET /v1/tickets/{ticket_id}/events` 返回按时间倒序排列的事件。查询先验证工单属于当前 API key 的租户，再按租户和工单 ID 过滤事件。事件包括：

- `ticket.created`：新工单建立。
- `ticket.create.replayed`：同一幂等键和同一请求内容再次提交，返回已有工单。
- `ticket.create.idempotency_conflict`：同一键提交了不同内容，操作被拒绝。
- `ticket.escalated`：处理团队或状态升级为人工支持。
- `ticket.escalation.replayed`：工单已经升级，再次请求没有重复改变状态。

每条记录含租户、工单 ID、事件类型、入口（`api-client` 或 `agent`）、结果、HTTP `request_id`、时间和安全的状态变更摘要。它不保存工单描述、对话全文或 API key。幂等键只以 SHA-256 指纹出现在审计记录中，原始键只用于工单重试存储。

旧演示工单创建于审计表加入之前，无法还原历史操作者或请求 ID；它们之后产生的新操作会有审计记录。API key 当前只能识别租户，不能识别租户里的具体员工，因此 `actor` 是调用入口，不等于已验证的个人身份。

## 手动查看

1. 打开 `http://localhost:8000/docs`，点击 Authorize 并输入当前租户的 API key。
2. 通过 `GET /v1/tickets` 找到一张工单。
3. 使用 `GET /v1/tickets/{ticket_id}/events` 查询操作历史。
4. 用相同 `Idempotency-Key` 和相同请求重试建单，事件中会新增 `ticket.create.replayed`，但返回的工单 ID 不变。

## 生产化边界

该实现是本地教学用的追加式记录：应用没有修改或删除审计事件的接口，但拥有数据库文件权限的人仍可修改 SQLite 文件。生产系统应把事件写入权限隔离的审计服务或 SIEM，明确保留期限、访问审计和个人身份映射，并对数据库备份与恢复制定策略。
