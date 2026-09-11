# lindaailabs-ops-agent

基于 **LangGraph + HITL + 动态 Skill** 的 Linux 运维 Agent（Phase 1 MVP）。

> 需求文档：`E:\doc\运维agent.md`

## 核心特性（Phase 1）
- **安全可控**：高危操作经 `interrupt()` 挂起，人工审批（`Command(resume=...)`）后才执行。
- **动态 Skill**：从独立 `skills` 仓库（默认 `D:/workspace/lindaailabs-skills`）扫描加载，无需重启。
- **多模型路由**：`config/settings.yaml` 维护模型池，按 `use_case` 实例化（凭证走 `${ENV}`，禁硬编码）。
- **FastAPI 入口**：`/chat` 发起任务、`/approve` 处理审批（mock）、`/reload_skills` 热加载。

## 目录结构
```
config/settings.yaml      # 全局配置（模型、skill_dir、checkpoint、approval）
src/
  graph/                  # state / nodes(含 interrupt) / workflow
  loader/skill_loader.py  # importlib 动态加载 + frontmatter 解析
  executor/               # Executor 抽象 + LocalExecutor（预留 SSH）
  models/router.py        # 多模型路由
  api/                    # FastAPI 服务
tests/                    # Skill 加载 & HITL 链路测试
```

## 快速开始
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # 填入 OPENAI_API_KEY
python -m src.main        # 启动 :8000
```

## 红线（见需求文档 §6）
1. 禁止硬编码凭证（仅 `settings.yaml` / 环境变量）。
2. 禁止跳过 Checkpointer（HITL 持久化底线，使用 SqliteSaver）。
3. 禁止在 `interrupt()` 之前执行副作用（Shell/写库/发请求必须在 resume 之后）。
4. 禁止 `eval()`/`exec()` 加载 Skill（仅 `importlib` + frontmatter 安全解析）。

## 路由约定
- 规划器（LLM 工具路由）：Skill 的 `SKILL.md` frontmatter 转成 tool schema，由 ChatModel 选择。
- 风险分级：`risk` 由 Skill 静态声明（`low`/`high`），`high` 触发审批中断。
- 执行后端：Skill 通过 `get_executor().run(cmd)` 执行，local/ssh 零侵入切换。

## 测试
```bash
pytest
```
