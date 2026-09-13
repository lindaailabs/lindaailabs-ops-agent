# lindaailabs-ops-agent

基于 **LangGraph + HITL + 动态 Skill** 的 Linux 运维 Agent（Phase 1 MVP）。

## 核心特性（Phase 1）
- **安全可控**：仅「高危 + 自动触发(`mode=automated`)」经 `interrupt()` 挂起，人工审批（`Command(resume=...)`）后才执行；人工对话 / 手动 CLI 触发视为已授权，直接执行不二次拦截。
- **动态 Skill**：从独立 `skills` 仓库扫描加载，无需重启。目录优先级：`SKILL_DIR` 环境变量 > `config/settings.yaml` 的 `skill_dir` > 默认 `./skills`（均支持 `${ENV_VAR}` 与 `~` 展开）。
- **多模型路由**：`config/settings.yaml` 维护模型池，按 `use_case` 实例化（凭证走 `${ENV}`，禁硬编码）。
- **FastAPI 入口**：`/chat` 发起任务、`/resume` 通用恢复（澄清/审批）、`/approve` 审批快捷、`/reload_skills` 热加载。
- **多轮交互**：Skill 的 `required_args` 缺失时，`clarify` 节点 `interrupt()` 反问收集；低风险一步直达，高危走「澄清 → 审批 → 执行」。
- **连续追问**：同一会话保留最近 16 条消息和 3 次带时间的检查摘要，支持基于 PID/JAR 的指代；新一轮独立初始化执行和审批状态。
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
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows PowerShell
# .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env      # 填入 OPENAI_API_KEY
python -m src.main        # 启动 :8000
```

全流程执行环境为 Linux。服务清单、CPU 和健康检查新增依赖 `psutil>=6,<8`，更新时需要同步更新两个仓库，并在运行 Agent 的虚拟环境内重新执行 `python -m pip install -r requirements.txt`，然后重启 API 或重新进入 REPL。单独热加载 Skill 不会更新 Agent 代码。

## Java 服务排查

| Skill | 用途 | 参数 |
| --- | --- | --- |
| `list_java_services` | JAR/主类、PID、运行时长、RSS、TCP 监听地址/端口 | 可选 `target` 筛选 |
| `check_cpu_usage` | 约 1 秒 CPU 采样、1/5/15 分钟负载、I/O wait、按 CPU 排序的进程 | 可选 `target`，PID 或服务身份 |
| `check_service_health` | 一个 Java 实例的进程状态、TCP 连通性及明确提供的 HTTP 健康接口 | 必填 `target`；可选 `health_url` |

`target` 支持 PID、JAR 文件名或主类，健康检查匹配多个实例时只返回候选，需进一步指定 PID。JAR/主类是观察到的进程身份，不保证等于业务名称；不会自动推断中文业务名到英文 JAR 的映射。

```bash
python -m src.cli --skill list_java_services
python -m src.cli --skill check_cpu_usage
python -m src.cli --skill check_service_health --args '{"target":"1234"}'
python -m src.cli --skill check_service_health --args '{"target":"1234","health_url":"http://127.0.0.1:8080/actuator/health"}'
```

上面的 PID 和地址仅为示例，必须替换为实际目标。HTTP 地址由用户指定，需匹配该 PID 当前可见的本机监听地址/端口；不支持凭证、查询参数、重定向或代理。没有健康接口时只报告进程与端口，TCP 连通不等于业务健康。

新采集器仅支持 Linux 本机，远程 `host` 会明确拒绝。数据覆盖当前可见的 PID/网络命名空间，权限不足会标记数据不完整。采集不会修改进程，也不执行 JVM attach、线程 dump 或堆 dump。CPU 进程百分比以单核为 100%，多线程可超过 100%；短时采样不等于根因诊断。采集接口说明见 [psutil 官方文档](https://psutil.io/)。

## Skill 目录配置
本地 Skill 目录（指向独立 `skills` 仓库）按以下优先级解析，越靠前优先级越高：
1. 环境变量 `SKILL_DIR`（生产推荐，便于零改动切换环境）；
2. `config/settings.yaml` 的 `skill_dir` 字段；
3. 默认值 `./skills`。

以上取值均支持 `${ENV_VAR}` 与 `~` 展开；相对路径按 `ops-agent` 项目根解析。默认配置假设两个仓库是兄弟目录：

```text
workspace/
  lindaailabs-ops-agent/
  lindaailabs-skills/
```

示例：
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
- 风险分级：`risk` 由 Skill 静态声明（`low`/`high`）；**审批闸门 = `risk:high` 且 `mode:automated`**（人工对话 / 手动 CLI 为 `interactive`，不审批）。
- **多轮澄清（全程 LLM 意图识别）**：Skill 声明 `required_args`（如 `disk_cleanup` 的 `path`）。若规划器未带齐，`clarify` 节点 `interrupt()` 抛出 `clarification_request` 反问；用户经 `/resume` 用**自然语言**回复（如"就是 /data 那个盘"），由 LLM（`simple_task` 模型）从回复中抽取出结构化参数，规则解析仅作兜底。该机制与风险等级解耦——低风险技能缺参数也会先澄清，但不触发审批。
- **使用者不懂系统，只能说大白话（这是能力需求，不是限制规则）**：使用者通常并不知道本 Agent 有哪些 `Skill`、要传什么参数、命令长什么样，所以他唯一能做的就是用自然语言描述诉求。入口（首轮 `planner` 与多轮 `clarify`）统一经 LLM 把"大白话"翻译成 Skill 声明的结构化参数。当用户的问法超出已注册能力、无法命中任何 Skill 时，`planner` 不报错死路，而是用 LLM 友好地说明"我能做什么、建议你怎么问"，帮助使用者把模糊诉求收敛到可执行操作。JSON 回复会被过滤为仅 Skill 声明的 `required_args`（+`host`），杜绝任意字段注入。
- 执行后端：Skill 通过 `get_executor().run(cmd)` 执行，local/ssh 零侵入切换。

## 审核触发规则与运维场景
**审核闸门 = `risk: high` 且处于「自动触发(`mode=automated`)」模式。**

核心原则：**人类是否在决策现场**。人工对话 / 手动 CLI 触发时人类就在场，其显式请求即视为授权，无需二次拦截；只有无人值守的定时 / 自动触发，才需要额外的人工审批闸门。

| 触发方式 | 简单（`low`） | 复杂（`high`） | 是否进审核节点 |
|---|---|---|---|
| 人工对话 `/chat`（默认 `interactive`） | 直执行 | 直执行（人类已授权） | ❌ |
| 定时任务 / 自动触发（`mode=automated`） | 直执行 | 挂起 `approval_request`，需 `/approve` 放行 | ✅ |
| 手动 CLI（终端，人类在场） | 直执行 | 交互确认 / `--yes` 后执行 | ❌（终端即授权） |

要点：
- **自动触发（定时任务等）**：`high` 技能先 `interrupt()` 挂起，返回 `pending_approval`，由人工经 `/approve` 或 `/resume` 放行后才产生副作用。
- **人工对话 / 手动 CLI**：即使 `high` 也直接执行（对话里一句话、或 CLI 手动敲命令即代表已授权）。CLI 仍保留 `--yes`/交互确认作为终端内的二次确认，但不走 `interrupt()` 审批节点。
- 切换触发模式：`/chat` 默认 `interactive`；定时任务调用时显式传 `"mode": "automated"` 即可启用审批闸门。

### 日常运维场景示例
**简单（任何方式都不触发审核，一步直达）：**
- "查下 web-01 的磁盘使用率" → `check_disk_usage`（`low`），对话 / 定时均直接返回 `df -h`。
- 定时巡检：每 5 分钟 `python -m src.cli --skill check_disk_usage`，静默直执行、无需审批。

**复杂（仅自动触发才需审批，人工对话 / CLI 直执行）：**
- 人工说"清理 /data 下 30 天前的日志" → 对话交互中直接执行 `disk_cleanup`（`high`），不挂审批。
- 定时任务触发 `disk_cleanup` → 挂起 `pending_approval`，值班人员 `/approve` 后才执行。
- "重启 nginx" / 批量删表 / kill 进程 / 改防火墙 → 均声明 `risk: high`；自动触发走审批，人工触发直执行。

> 注意：多轮澄清（缺参数反问）与审核是两套独立机制。自动触发且缺必填参数时，会先「澄清 → 再审批 → 执行」；人工对话缺参数只澄清、不审批。

## 多轮对话示例（HTTP）
```bash
# 1) 用户说"清理磁盘"但未给路径 -> 触发澄清中断
curl -X POST localhost:8000/chat -H 'Content-Type: application/json' \
  -d '{"message":"清理一下磁盘","thread_id":"t1","mode":"automated"}'
# -> {"status":"pending_clarification","clarification_request":{"type":"clarification_request","missing":["path"],...}}

# 2) 用户回复路径 -> 进入高危审批中断
curl -X POST localhost:8000/resume -H 'Content-Type: application/json' \
  -d '{"thread_id":"t1","response":"/data"}'
# -> {"status":"pending_approval",...}

# 3) 审批通过 -> 执行
curl -X POST localhost:8000/approve -H 'Content-Type: application/json' \
  -d '{"thread_id":"t1","approved":true,"comment":"同意"}'
# -> {"status":"done","answer":"清理前评估完成：/data 当前占用约 ...。本次仅执行 dry-run，没有删除任何文件。"}
```

`answer` 是面向运维人员的摘要，不会直接返回 Skill executor 的内部 JSON。比如磁盘检查会展示检查数量、最高使用率、重点挂载点、可用空间和告警结论；清理技能会明确说明目标路径的占用量，并注明当前阶段只是 dry-run、没有删除文件。完整的 `stdout`、`stderr` 和退出码仍保存在图状态的 `execution_result` 中，供日志和排障使用。

## 测试
```bash
python -m pytest
python -m mypy src
```

## 手动触发（绕过 LLM，无需 API Key）
```bash
python -m src.cli --skill check_disk_usage
python -m src.cli --skill disk_cleanup --args '{"path":"/tmp"}' --yes
```

## 对话 REPL（不起端口，直接对接 LLM 多轮）

不想起 HTTP 服务时，可用 REPL 模式在终端直接与 Agent 多轮对话。它复用与 `/chat` **完全相同**的 graph（planner + clarify 走 LLM + executor），只是以 stdin/stdout 驱动，无需监听端口、无需前端。

```bash
# 默认 interactive：人在终端即视为已授权，高危技能直接执行不二次审批
python -m src.cli --repl

# automated：演示审批闸门（高危会要求终端内 y/N 确认）
python -m src.cli --repl --mode automated
```

对话示例（interactive）：
```
你> 清理一下磁盘
Agent> 需要补充参数：path（目标路径）
>> 就是 /data 那个盘
Agent> 清理前评估完成：/data 当前占用约 12G。 本次仅执行 dry-run，没有删除任何文件。
你> exit
```

- 多轮澄清、自然语言补参、缺参数反问等机制与 HTTP `/chat` 行为一致；
- REPL 完成检查后继续复用会话，输入 `/new` 开始新会话；历史仅用于指代和解释，不自动重复执行。请求最新数据时重新采集。
- HTTP `/chat` 未传 `thread_id` 时创建独立会话并在所有响应中返回 ID；后续追问沿用该 ID。待澄清/审批时使用 `/resume`，新的 `/chat` 返回 409。
- 退出输入 `exit` / `quit` 或 `Ctrl-D`；
- 与"手动触发"的区别：手动触发（`--skill`）绕过 LLM 规划器、按已知 skill 直跑；REPL 走完整 LLM 意图识别链路。

可在同一 REPL 依次说“列出 Java 服务”“检查 order.jar 的 CPU”“检查这个服务是否存活”。规划器结合最近的 PID/JAR 结果解析指代；存在多个候选会反问。当前每轮最多执行一个 Skill，组合排查需逐步进行，尚未提供线程/GC 诊断能力。

## 实测示例（Linux / macOS）

项目以源码形式直接运行（无需编译、无需打包），REPL 模式实测效果如下（截图运行于 macOS）：

```bash
python -m src.cli --repl --mode automated
```

![对话 REPL 实测](assets/server-cli-demo.png)

- 自然语言提问「看下系统负载」→ 触发 `check_cpu_usage`，约 1 秒 CPU 采样，返回逻辑核数、1/5/15 分钟负载、I/O wait 与按 CPU 排序的进程；
- 提问「磁盘情况呢」→ 触发 `check_disk_usage`，汇总各挂载点使用率并给出告警结论，自动跳过容量为 0 的伪挂载（如 `devfs`），不会误报 100%。

采集层基于 `psutil` 实现，Linux 与 macOS 均可运行，不依赖 `free`、`df`、GNU `ps` 等平台专属命令。
