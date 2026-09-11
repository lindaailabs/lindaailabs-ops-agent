# lindaailabs-ops-agent

基于 **LangGraph + HITL + 动态 Skill** 的 Linux 运维 Agent（Phase 1 MVP）。

## 核心特性（Phase 1）
- **安全可控**：高危操作经 `interrupt()` 挂起，人工审批（`Command(resume=...)`）后才执行。
- **动态 Skill**：从独立 `skills` 仓库扫描加载，无需重启。目录优先级：`SKILL_DIR` 环境变量 > `config/settings.yaml` 的 `skill_dir` > 默认 `./skills`（均支持 `${ENV_VAR}` 与 `~` 展开）。
- **多模型路由**：`config/settings.yaml` 维护模型池，按 `use_case` 实例化（凭证走 `${ENV}`，禁硬编码）。
- **FastAPI 入口**：`/chat` 发起任务、`/resume` 通用恢复（澄清/审批）、`/approve` 审批快捷、`/reload_skills` 热加载。
- **多轮交互**：Skill 的 `required_args` 缺失时，`clarify` 节点 `interrupt()` 反问收集；低风险一步直达，高危走「澄清 → 审批 → 执行」。
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

## 模型配置（config/settings.yaml）
模型不在代码里写死，统一在 `config/settings.yaml` 的 `models:` 列表维护，运行时由 `ModelRouter` 按 `use_case` 选择并实例化（凭证用 `${ENV_VAR}` 占位，由 python-dotenv 解析，禁止硬编码明文）。

```yaml
models:
  - name: "qwen-turbo"
    type: "openai"
    api_key: "${OPENAI_API_KEY}"
    base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
    use_case: "simple_task"        # 简单任务路由到此模型
  - name: "gpt-4o"
    type: "openai"
    api_key: "${OPENAI_API_KEY}"
    base_url: "https://api.openai.com/v1"
    use_case: "complex_reasoning"  # 复杂推理（如规划器选 Skill）路由到此模型
```

- 切换/新增模型：在 `models:` 下加一项，填 `name` / `type` / `api_key` / `base_url` / `use_case`，无需改代码。
- `use_case` 是路由键：规划器（`planner` 节点）固定使用 `complex_reasoning`；其余能力可按需在 `router.get(use_case=...)` 调用时指定。
- 凭证只走 `${ENV_VAR}` + `.env`：复制 `.env.example` 为 `.env` 填 `OPENAI_API_KEY` 即可，`.env` 已被 `.gitignore` 忽略。

## 路由约定
- 规划器（LLM 工具路由）：Skill 的 `SKILL.md` frontmatter 转成 tool schema，由 ChatModel 选择；tool 参数由该 Skill 的 `required_args` + `host` 动态生成。
- 风险分级：`risk` 由 Skill 静态声明（`low`/`high`），`high` 触发审批中断。
- **多轮澄清**：Skill 声明 `required_args`（如 `disk_cleanup` 的 `path`）。若规划器未带齐，`clarify` 节点 `interrupt()` 抛出 `clarification_request` 反问；用户经 `/resume` 回复后合并参数并继续。该机制与风险等级解耦——低风险技能缺参数也会先澄清，但不触发审批。
- 执行后端：Skill 通过 `get_executor().run(cmd)` 执行，local/ssh 零侵入切换。

## 审核触发规则与运维场景
**是否触发人工审核，唯一依据是 Skill 自身声明的 `risk` 字段，与"通过对话触发还是定时/CLI 直接触发"无关。**

| 任务风险 | 对话触发（`/chat`） | 定时任务 / CLI 直触发 | 是否审核 |
|---|---|---|---|
| `low`（简单） | 解析后一步执行 | `python -m src.cli --skill <name>` 直接跑 | ❌ 不审核 |
| `high`（复杂） | 解析后挂起审核节点 | CLI 需 `--yes` 或交互确认 | ✅ 触发审核 |

要点：
- **简单任务**：人工在对话里说一句、或定时任务 / CLI 直接调，都**直执行、不进审核节点**。
- **复杂任务**：无论哪种入口，都会先 `interrupt()` 挂起，等人工放行（`/approve` 或 CLI `--yes` / 交互确认）后才产生副作用。

### 日常运维场景示例
**简单（不触发审核，一步直达）：**
- "查下 web-01 的磁盘使用率" → `check_disk_usage`（`low`），直接返回 `df -h` 结果。
- 定时巡检：每 5 分钟 `python -m src.cli --skill check_disk_usage`，静默直执行、无需审批。
- "看下 192.168.1.10 的内存占用" → 类似只读巡检 Skill（`low`），对话 / 定时均可直跑。

**复杂（触发审核节点，需人工放行）：**
- "清理 /data 下 30 天前的日志" → `disk_cleanup`（`high`），先 `pending_approval`，审批通过才执行。
- "重启 nginx 服务" → 高危 Skill（`high`），无论对话还是 CLI 都先过审核。
- 批量删表 / kill 进程 / 修改防火墙规则 → 均声明 `risk: high`，强制人工确认。

> 注意：多轮澄清（缺参数反问）与审核是两套独立机制。复杂任务若同时缺必填参数，会先「澄清 → 再审批 → 执行」；简单任务缺参数只会澄清、不审批。

## 多轮对话示例（HTTP）
```bash
# 1) 用户说"清理磁盘"但未给路径 -> 触发澄清中断
curl -X POST localhost:8000/chat -H 'Content-Type: application/json' \
  -d '{"message":"清理一下磁盘","thread_id":"t1"}'
# -> {"status":"pending_clarification","clarification_request":{"type":"clarification_request","missing":["path"],...}}

# 2) 用户回复路径 -> 进入高危审批中断
curl -X POST localhost:8000/resume -H 'Content-Type: application/json' \
  -d '{"thread_id":"t1","response":"/data"}'
# -> {"status":"pending_approval",...}

# 3) 审批通过 -> 执行
curl -X POST localhost:8000/approve -H 'Content-Type: application/json' \
  -d '{"thread_id":"t1","approved":true,"comment":"同意"}'
# -> {"status":"done","answer":"[disk_cleanup] 执行完成：..."}
```

## 测试
```bash
pytest
```

## 手动触发（绕过 LLM，无需 API Key）
```bash
python -m src.cli --skill check_disk_usage
python -m src.cli --skill disk_cleanup --args '{"path":"/tmp"}' --yes
```
