"""图组装与编译。"""
from typing import Any, Callable, Dict, Optional

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from src.executor.base import Executor
from src.executor.registry import set_executor
from src.graph.nodes import (
    build_clarify_node,
    build_executor_node,
    build_planner_node,
    finalize_node,
)
from src.graph.state import OpsAgentState
from src.loader.skill_loader import Skill
from src.models.router import ModelRouter


def _route_after_execute(state: Dict[str, Any]) -> str:
    if state.get("approved") is False:
        return "abort"
    return "finalize"


def _route_after_planner(state: Dict[str, Any]) -> str:
    # 规划器没匹配到任何 Skill（使用者问得太宽泛 / 超出能力范围）：
    # 直接进入 finalize，由 planner 预置的自然语言回答收尾，不报错死路。
    if state.get("selected_skill") is None:
        return "finalize"
    return "clarify"


def build_graph(
    skills: Dict[str, Skill],
    router: ModelRouter,
    executor: Executor,
    checkpointer: SqliteSaver,
    planner_node: Optional[Callable] = None,
    clarify_node: Optional[Callable] = None,
    executor_node: Optional[Callable] = None,
):
    """组装并编译 StateGraph。

    planner_node / clarify_node / executor_node 可注入（测试时替换为 fake），
    默认按 skills/router 构建。多轮交互链路：planner -> clarify -> execute。
    """
    set_executor(executor)  # 确保技能内 get_executor() 拿到正确的后端
    planner = planner_node or build_planner_node(skills, router)
    clarify = clarify_node or build_clarify_node(skills, router)
    exe = executor_node or build_executor_node(skills)

    g = StateGraph(OpsAgentState)
    g.add_node("planner", planner)
    g.add_node("clarify", clarify)
    g.add_node("execute", exe)
    g.add_node("abort", lambda s: {"final_answer": s.get("final_answer") or "操作被拒绝"})
    g.add_node("finalize", finalize_node)

    g.add_edge(START, "planner")
    g.add_conditional_edges(
        "planner",
        _route_after_planner,
        {"clarify": "clarify", "finalize": "finalize"},
    )
    g.add_edge("clarify", "execute")
    g.add_conditional_edges(
        "execute", _route_after_execute, {"abort": "abort", "finalize": "finalize"}
    )
    g.add_edge("abort", END)
    g.add_edge("finalize", END)

    return g.compile(checkpointer=checkpointer)
