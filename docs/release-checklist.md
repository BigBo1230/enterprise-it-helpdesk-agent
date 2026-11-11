# GitHub 发布清单

这份清单用于把本地学习项目安全地发布为可展示的作品集仓库。发布前要证明三件事：代码可复现、配置不泄密、自动检查能在新环境运行。

## 1. 本地质量门禁

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run pytest
cd frontend
npm ci
npm run lint
npm audit --omit=dev --audit-level=high
npm run build
```

Docker 还应能构建后端和生产前端：

```bash
docker build -t enterprise-it-helpdesk-agent:local .
docker build --target production -t enterprise-it-helpdesk-frontend:local frontend
docker compose config --quiet
```

Qdrant 客户端和服务端的次版本差不要超过 1。升级持久化服务端前先阅读官方迁移说明并备份数据卷；不要直接跨过不兼容的数据格式版本。

## 2. 密钥与数据边界

- `.env`、数据库、Qdrant 数据和构建产物不得提交。
- `.env.example` 只能包含空值、占位符和本地 mock token。
- 浏览器租户密钥只保存在 `sessionStorage`，不要截图或复制到 Issue、README 和提交记录。
- 提交前运行 `git status --short`，逐项确认新增文件属于项目源码、测试或文档。

`npm audit --omit=dev` 检查会进入生产静态包的依赖。当前剩余的开发依赖告警来自 lint 工具的传递依赖，生产依赖审计为零；不要为了清零开发告警盲目运行 `npm audit fix --force`。

## 3. Git 远程

本项目保留原模板的 MIT 许可与署名。发布时将原模板远程命名为 `upstream`，把你自己的 GitHub 仓库设置为 `origin`：

```bash
git remote rename origin upstream
git remote add origin https://github.com/<your-account>/enterprise-it-helpdesk-agent.git
git remote -v
```

只有当 `origin` 明确指向你的仓库后，才执行首次推送。

## 4. GitHub Actions

`.github/workflows/ci.yml` 会检查 Python 格式、静态类型、测试、前端 lint/构建，以及后端和前端 Docker 镜像构建。首次推送后，在仓库的 **Actions** 页面确认两个代码任务和容器任务全部变绿。

## 5. 仓库展示信息

建议仓库名使用 `enterprise-it-helpdesk-agent`，简介可写：

> Multi-tenant enterprise IT helpdesk agent with RAG citations, LangGraph tool calling, reliable ticket integrations, security evals, and Docker deployment.

推荐 Topics：`rag`、`langgraph`、`fastapi`、`react`、`qdrant`、`ai-agent`、`it-helpdesk`、`docker`。
