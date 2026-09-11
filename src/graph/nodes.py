"""节点逻辑。

关键红线（见运维agent.md §6）：
- interrupt() 重跑当前节点，因此所有副作用（Shell/写库/发请求）必须放在
  interrupt() 调用之后，绝不能在 interrupt 之前的代码里产生副作用。
- 模型路由经配置文件驱动，不写死模型名。
"""
from typing import Any, Callable, Dict, Optional

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import interrupt

from src.loader.skill_loader import Skill
from src.models.router import ModelRouter


def build_planner_node(
    skills: Dict[str, Skill], router: ModelRouter
) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """规划器：LLM 工具路由。把每个 Skill 的 SKILL.md 转成 tool schema，由 ChatModel 选择。"""

    def planner(state: Dict[str, Any]) -> Dict[str, Any]:
        llm = router.get(use_case="complex_reasoning")
        tools = [
            {
                "type": "function",
                "function": {
                    "name": s.name,
                    "description": f"{s.description} | 触发条件: {s.trigger}",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "host": {"type": "string", "description": "目标主机标识"}
                        },
                        "required": [],
                    },
                },
            }
            for s in skills.values()
        ]
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


def build_executor_node(
    skills: Dict[str, Skill],
) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """执行节点：高危操作先 interrupt 等待人工审批，副作用仅在 resume 之后发生。"""

    def execute_node(state: Dict[str, Any]) -> Dict[str, Any]:
        skill = skills.get(state.get("selected_skill"))
        if skill is None:
            return {"error": "selected_skill 缺失"}

        # HITL：高危操作先中断。interrupt 的参数会作为"待审批请求"返回给调用方。
        # 仅在用户通过 Command(resume=...) 恢复后，下面才会继续执行（无副作用残留）。
        if state.get("risk_level") == "high":
            approval = interrupt(
                {
                    "type": "approval_request",
                    "skill": state.get("selected_skill"),
                    "command": state.get("command"),
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
