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
from typing import Any, Callable, Dict, List, Optional

from langchain_core.messages import AIMessage, HumanMessage
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
            return {
                "error": "无法从请求中解析出可执行的操作",
                "final_answer": "抱歉，我暂时无法处理该请求，请描述得更具体一些。",
            }
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


def build_clarify_node(
    skills: Dict[str, Skill],
) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """澄清节点：若 Skill 的 required_args 未齐，则 interrupt 收集缺失参数。

    多轮机制：首次运行检测到缺失 -> interrupt(clarification_request) 挂起；
    用户通过 /resume 回复后，节点重跑，interrupt() 直接返回回复，合并进 skill_args
    并返回（状态落库），随后流转到 execute 节点。
    """

    def clarify_node(state: Dict[str, Any]) -> Dict[str, Any]:
        skill = skills.get(state.get("selected_skill"))
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
        merged = {**args, **_merge_clarification(reply, missing)}
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
        skill = skills.get(state.get("selected_skill"))
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
    res = state.get("execution_result") or {}
    skill = state.get("selected_skill", "未知")
    return {"final_answer": f"[{skill}] 执行完成：{res}"}
