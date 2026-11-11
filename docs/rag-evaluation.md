# RAG 检索评测

评测样例位于 `tests/evals/rag_golden.jsonl`。每条样例包含问题和人工标注的相关来源。

## 指标

- `Recall@K`：前 K 个结果找回了多少个相关来源。越高越好。
- `MRR`：第一个相关来源的排名倒数的平均值。相关来源越靠前越好。
- `Precision@K`：前 K 个结果中有多少比例相关。越高越好。
- `Citation source accuracy`：Agent 答案中的 `[source]` 标签有多少指向本题标注为相关、且实际检索到的来源。它只检查来源标签，不能自动判断来源是否真正支持该句话。

## 运行

在项目根目录执行：

```powershell
docker compose run --rm --no-deps -v "D:\FDE-Enterprise-IT-Helpdesk\tests:/app/tests:ro" api uv run python -m tests.evals.rag_runner --tenant demo
```

加入 `--with-answers` 会调用配置的聊天模型生成答案并评估引用标签，按当前样例数会产生模型调用费用：

```powershell
docker compose run --rm --no-deps -v "D:\FDE-Enterprise-IT-Helpdesk\tests:/app/tests:ro" api uv run python -m tests.evals.rag_runner --tenant demo --with-answers
```

结果只代表这组小型教学样例。扩展评测时，应覆盖不同部门、文档版本、无答案问题、跨租户查询和对抗提示，并由人工审核相关来源标注。
