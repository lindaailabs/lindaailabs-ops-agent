"""节点逻辑。

关键红线（见运维agent.md §6）：
- interrupt() 重跑当前节点，因此所有副作用（Shell/写库/发请求）必须放在
  interrupt() 调用之后，绝不能在 interrupt 之前的代码里产生副作用。
- 模型路由经配置文件驱动，不写死模型名。

多轮交互设计：把「澄清」与「审批」拆成两个独立节点（clarify / execute），
各自最多中断一次，状态在节点间流转，避免同一节点内多次 interrupt 重入导致的
resume 值错位问题。
"""
import json
import re
from typing import Any, Callable, Dict, List, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.types import interrupt

from src.loader.skill_loader import Skill
from src.models.router import ModelRouter


def _merge_clarification(reply: Any, missing: List[str]) -> Dict[str, Any]:
    """把用户回复（resume 值）合并进 skill_args。

    - reply 本身是 dict -> 直接采用；
    - reply 是 JSON 字符串且能解析为 dict -> 采用；
    - 单一缺失参数 -> 整段文本作为该参数值；
    - 否则 -> 空（交由 Skill 默认值处理）。
    """
    if isinstance(reply, dict):
        return reply
    text = reply if isinstance(reply, str) else str(reply)
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    if len(missing) == 1:
        return {missing[0]: text}
    return {}


def build_planner_node(
    skills: Dict[str, Skill], router: ModelRouter
) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """规划器：LLM 工具路由。把每个 Skill 的 SKILL.md 转成 tool schema，由 ChatModel 选择。

    tool 的参数由该 Skill 的 required_args + host 动态生成，让 LLM 能一次性带出已知参数，
    减少不必要的澄清轮次。

    设计前提（见运维agent.md 语义澄清）：使用者通常并不了解这套系统——不知道有哪些
    Skill、要传什么参数、命令长什么样，所以他唯一能做的就是用自然语言描述诉求。
    planner 的职责就是把这个"大白话"翻译成"选哪个 Skill + 带哪些参数"。当用户的问法
    超出已注册能力、无法命中任何 Skill 时，不报错死路，而是用 LLM 友好地说明本 Agent
    能做什么、建议用户怎么问，帮助把模糊诉求收敛到可执行操作。
    """

    def planner(state: Dict[str, Any]) -> Dict[str, Any]:
        llm = router.get(use_case="complex_reasoning")
        tools = []
        for s in skills.values():
            properties = {
                "host": {"type": "string", "description": "目标主机标识（默认 localhost）"}
            }
            for r in s.required_args:
                properties[r] = {"type": "string", "description": f"{s.name} 所需参数：{r}"}
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": s.name,
                        "description": f"{s.description} | 触发条件: {s.trigger}",
                        "parameters": {
                            "type": "object",
                            "properties": properties,
                            "required": list(s.required_args),
                        },
                    },
                }
            )
        llm_with_tools = llm.bind(tools=tools)
        msg: AIMessage = llm_with_tools.invoke(
            [HumanMessage(content=state["user_input"])]
        )
        if not msg.tool_calls:
            # 用户的问法没有命中任何已注册 Skill。
            # 对不熟悉系统的使用者而言这很常见（他并不知道有哪些能力、该怎么问）。
            # 此时不报错死路，而是让 LLM 生成一段自然语言回答，并附上本 Agent 能做的事，
            # 帮助用户把模糊诉求收敛到可执行操作上。
            caps = "; ".join(f"{s.name}：{s.description}" for s in skills.values())
            fallback = llm.invoke(
                [
                    SystemMessage(
                        content=(
                            "你是 Linux 运维助手。用户的请求无法映射到任何已注册操作。"
                            "请用自然语言友好地说明你目前能做的事（基于下列能力清单），"
                            "并引导用户用更具体的描述来表达需求。不要编造任何命令。"
                        )
                    ),
                    HumanMessage(
                        content=f"用户请求：{state['user_input']}\n可用能力：{caps}"
                    ),
                ]
            )
            return {"final_answer": fallback.content, "selected_skill": None}
        tc = msg.tool_calls[0]
        skill = skills.get(tc["name"])
        if skill is None:
            return {"error": f"未知 Skill: {tc['name']}"}
        return {
            "selected_skill": skill.name,
            "skill_args": tc.get("args", {}) or {},
            "risk_level": skill.risk,  # 风险来自 Skill 静态声明
            "command": {"skill": skill.name, "args": tc.get("args", {}) or {}},
        }

    return planner


def _extract_args_with_llm(
    router, skill_name: str, missing: List[str], reply_text: str
) -> Optional[Dict[str, Any]]:
    """用 LLM 从自然语言回复中抽取缺失参数。

    多轮澄清的核心：用户用自然语言回复（如"就是 /data 那个盘"），由 LLM 抽取成
    结构化参数（{"path": "/data"}）。失败返回 None，交由规则解析兜底。
    只产出 Skill 声明的 required_args 对应键值，不允许注入任意字段。
    """
    if router is None:
        return None
    try:
        llm = router.get(use_case="simple_task")
        prompt = (
            f"你是运维助手的参数抽取器。技能「{skill_name}」需要参数：{missing}。\n"
            f"用户刚才的回复是：\"{reply_text}\"\n"
            f"请只输出一个 JSON 对象，键为上述参数名，值为从回复中抽取的内容，"
            f"不要输出任何解释文字。"
        )
        out = llm.invoke(prompt).content
        m = re.search(r"\{.*\}", out, re.DOTALL)
        if not m:
            return None
        obj = json.loads(m.group(0))
        if isinstance(obj, dict):
            return {k: obj[k] for k in missing if k in obj}
        return None
    except Exception:
        return None


def build_clarify_node(
    skills: Dict[str, Skill],
    router=None,
) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """澄清节点：若 Skill 的 required_args 未齐，则 interrupt 收集缺失参数。

    多轮机制：首次运行检测到缺失 -> interrupt(clarification_request) 挂起；
    用户通过 /resume 回复后，节点重跑，interrupt() 直接返回回复，合并进 skill_args
    并返回（状态落库），随后流转到 execute 节点。

    回复理解：使用者不熟悉系统内部，只能用大白话描述（如"就是 /data 那个盘"），
    由 LLM 从自然语言抽取出结构化参数；规则解析兜底。这是"能力需求"（系统必须理解人话），
    不是"限制用户"的规则。JSON 回复会被过滤为仅 Skill 声明的参数，防注入。
    """

    def clarify_node(state: Dict[str, Any]) -> Dict[str, Any]:
        selected_skill = state.get("selected_skill")
        if not isinstance(selected_skill, str):
            return {"error": "selected_skill 缺失"}
        skill = skills.get(selected_skill)
        if skill is None:
            return {"error": "selected_skill 缺失"}

        args = dict(state.get("skill_args") or {})
        missing = [r for r in skill.required_args if not args.get(r)]
        if not missing:
            return {}  # 参数齐全，跳过澄清

        reply = interrupt(
            {
                "type": "clarification_request",
                "skill": skill.name,
                "missing": missing,
                "current": args,
            }
        )
        # ---- resume 之后才执行：合并用户回复 ----
        # 使用者不懂系统内部（不知道有哪些 skill、要传什么参数、命令长什么样），
        # 只能用大白话问。结构化参数一律由 LLM 或规则从自然语言抽取，
        # 且只允许 Skill 声明的 required_args（+host），杜绝任意字段注入。
        allowed = set(skill.required_args) | set(args.keys()) | {"host"}
        if isinstance(reply, dict):
            merged = {**args, **{k: v for k, v in reply.items() if k in allowed}}
        else:
            merged = {**args}
            llm_extra = _extract_args_with_llm(router, skill.name, missing, str(reply))
            if llm_extra:
                merged.update(llm_extra)
            else:
                merged.update(_merge_clarification(reply, missing))
        return {"skill_args": merged}

    return clarify_node


def build_executor_node(
    skills: Dict[str, Skill],
) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """执行节点：仅当『高危 AND 自动触发(automated)』时先 interrupt 等待人工审批。

    人工对话交互(interactive)中即使高危也直接执行——人类已在场即视为已授权，
    不再二次拦截。副作用始终放在 interrupt 之后。
    """

    def execute_node(state: Dict[str, Any]) -> Dict[str, Any]:
        selected_skill = state.get("selected_skill")
        if not isinstance(selected_skill, str):
            return {"error": "selected_skill 缺失"}
        skill = skills.get(selected_skill)
        if skill is None:
            return {"error": "selected_skill 缺失"}

        # HITL：仅【高危 + 自动触发】才挂起审批。人工对话交互(interactive)不加闸门，
        # 因为人类的显式请求本身就是授权。interrupt 的参数作为"待审批请求"返回调用方，
        # 仅在用户通过 Command(resume=...) 恢复后下面才继续执行（无副作用残留）。
        needs_approval = (
            state.get("risk_level") == "high"
            and state.get("mode", "interactive") == "automated"
        )
        if needs_approval:
            approval = interrupt(
                {
                    "type": "approval_request",
                    "skill": state.get("selected_skill"),
                    "command": state.get("command"),
                    "mode": "automated",
                }
            )
            # ---- 以下为 resume 之后才执行的代码 ----
            if not approval or not approval.get("approved"):
                comment = (approval or {}).get("comment", "")
                return {
                    "approved": False,
                    "final_answer": f"操作已被拒绝，未执行。{comment}",
                }

        # 唯一副作用点：必须在 interrupt 之后
        result = skill.execute(state)
        return {"execution_result": result, "approved": True}

    return execute_node


def finalize_node(state: Dict[str, Any]) -> Dict[str, Any]:
    if state.get("error"):
        return {"final_answer": f"执行出错：{state['error']}"}
    # planner 的"无匹配"对话式回落已在 final_answer 预置自然语言回答（无 execution_result），
    # 直接透传，不套用"[skill] 执行完成"模板。
    if state.get("final_answer") and not state.get("execution_result"):
        return {"final_answer": state["final_answer"]}
    res = state.get("execution_result") or {}
    skill = state.get("selected_skill", "未知")
    return {"final_answer": f"[{skill}] 执行完成：{res}"}
