# AI Short Drama Canvas Orchestrator

面向短剧和内容创作场景的 AI 创作编排后端。系统接收用户的剧情创意，通过多阶段 Agent 工作流生成结构化剧本、角色设定、场景设定、分镜和视觉 Prompt，形成可回填的创作画布，再交由第三方创作平台生成最终视频。

## 核心能力

- **LangGraph 工作流**：剧本解析、角色与场景设定、分镜规划、Prompt 优化和一致性审核的状态流转。
- **人工审批与断点恢复**：脚本和分镜支持审核、修改、拒绝；通过 Checkpointer 保存工作流上下文。
- **结构化模型输出**：基于 LangChain Core 与 Pydantic 约束剧本、角色卡、场景卡和分镜表，并校验 ID、时长和生成参数一致性。
- **模型网关**：支持第三方 LLM/VLM 模型配置、能力路由、连接检测、Token、延迟和费用统计。
- **异步任务编排**：通过 Celery/Redis 调度图片、视频和配音任务，支持幂等、重试、超时补偿和局部恢复。
- **资产回填**：使用 S3 归档参考素材和生成资产，通过预签名 URL、画布绑定和失效标记完成资产管理。

## 技术栈

Python、FastAPI、LangGraph、LangChain Core、Pydantic、Celery、Redis、PostgreSQL（生产环境适配）、AWS S3、WebSocket、HTTPX、OpenAI-Compatible API。

## 项目结构

```text
app/
  api.py              REST API、WebSocket 事件推送
  graph.py            LangGraph 状态图与条件路由
  domain.py           领域模型和工作流状态
  routed_gateway.py   结构化模型调用与输出校验
  gateway_admin.py    模型供应商、路由和用量管理
  services.py         运行编排、媒体任务和画布回填
  providers.py        模型、资产存储和媒体适配器
  repositories.py     仓储抽象（本地演示使用内存实现）
  tasks.py            Celery 任务入口
```

## 本地运行

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

启动后访问 `http://127.0.0.1:8000/docs` 查看 API 文档，访问 `http://127.0.0.1:8000/` 查看演示页面。

复制 `.env.example` 为 `.env` 后，可配置 Redis、S3 和模型网关加密密钥。开发环境默认使用内存仓储和模拟模型；生产环境可替换为 PostgreSQL 仓储、S3 和真实模型适配器。

## 主要接口

```text
POST /api/projects
POST /api/projects/{project_id}/runs
GET  /api/runs/{run_id}
POST /api/runs/{run_id}/approve
POST /api/runs/{run_id}/cancel
POST /api/runs/{run_id}/shots/{shot_id}/retry
GET  /api/runs/{run_id}/events
WS   /api/ws/runs/{run_id}
```

## 安全说明

模型 API Key 只保存加密后的值，接口返回时仅展示脱敏信息；`.env`、缓存文件和 Python 字节码均通过 `.gitignore` 排除，不应将真实密钥提交到仓库。
