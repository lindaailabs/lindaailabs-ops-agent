# lindaailabs-ops-agent

基于 **LangGraph + HITL + 动态 Skill** 的 Linux 运维 Agent（Phase 1 MVP）。

## 核心特性（Phase 1）
- **安全可控**：高危操作经 `interrupt()` 挂起，人工审批（`Command(resume=...)`）后才执行。
- **动态 Skill**：从独立 `skills` 仓库扫描加载，无需重启。目录优先级：`SKILL_DIR` 环境变量 > `config/settings.yaml` 的 `skill_dir` > 默认 `./skills`（均支持 `${ENV_VAR}` 与 `~` 展开）。
- **多模型路由**：`config/settings.yaml` 维护模型池，按 `use_case` 实例化（凭证走 `${ENV}`，禁硬编码）。
- **FastAPI 入口**：`/chat` 发起任务、`/approve` 处理审批（mock）、`/reload_skills` 热加载。
- **手动触发 CLI**：`python -m src.cli --skill <name>` 绕过 LLM 规划器直接执行（不依赖 `OPENAI_API_KEY`，高危需 `--yes` 或交互确认）。

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

## Skill 目录配置
本地 Skill 目录（指向独立 `skills` 仓库）按以下优先级解析，越靠前优先级越高：
1. 环境变量 `SKILL_DIR`（生产推荐，便于零改动切换环境）；
2. `config/settings.yaml` 的 `skill_dir` 字段；
3. 默认值 `./skills`。

以上取值均支持 `${ENV_VAR}` 与 `~` 展开。示例：
```bash
# Linux
export SKILL_DIR=~/lindaailabs-skills
# 或任意绝对路径
export SKILL_DIR=/opt/lindaailabs-skills
python -m src.main
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

## 手动触发（绕过 LLM，无需 API Key）
```bash
python -m src.cli --skill check_disk_usage
python -m src.cli --skill disk_cleanup --args '{"path":"/tmp"}' --yes
```
