"""HITL 链路测试：中断 -> 恢复 -> 执行 的完整流程（含高危拦截与拒绝分支）。

使用 fake planner 节点，避免依赖真实 LLM key；执行用 LocalExecutor，
命令在 Windows 本地会报错但被 LocalExecutor 捕获，不影响流程断言。
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langgraph.checkpoint.sqlite import SqliteSaver  # noqa: E402
from langgraph.types import Command  # noqa: E402

from src.executor import LocalExecutor, set_executor  # noqa: E402
from src.graph.workflow import build_graph  # noqa: E402
from src.loader.skill_loader import SkillLoader  # noqa: E402

SKILLS_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "lindaailabs-skills")
)


def _make_planner(skills):
    def planner(state):
        name = "disk_cleanup" if "清理" in state["user_input"] else "check_disk_usage"
        sk = skills[name]
        return {
            "selected_skill": name,
            "skill_args": {"host": "localhost"},
            "risk_level": sk.risk,
            "command": {"skill": name, "args": {}},
        }

    return planner


def _build():
    skills = SkillLoader(SKILLS_DIR).scan()
    set_executor(LocalExecutor())
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    saver = SqliteSaver(conn)
    graph = build_graph(
        skills,
        router=None,
        executor=LocalExecutor(),
        checkpointer=saver,
        planner_node=_make_planner(skills),
    )
    return graph


def test_low_risk_runs_without_approval():
    graph = _build()
    cfg = {"configurable": {"thread_id": "t1"}}
    res = graph.invoke({"user_input": "查看磁盘使用率"}, cfg)
    assert res["approved"] is True
    assert res["final_answer"]


def test_high_risk_interrupt_then_approve():
    graph = _build()
    cfg = {"configurable": {"thread_id": "t2"}}
    graph.invoke({"user_input": "清理一下磁盘"}, cfg)
    snap = graph.get_state(cfg)
    assert snap.next  # 被 interrupt 挂起

    resumed = graph.invoke(Command(resume={"approved": True, "comment": "同意"}), cfg)
    assert resumed["approved"] is True
    assert resumed["final_answer"]


def test_high_risk_reject():
    graph = _build()
    cfg = {"configurable": {"thread_id": "t3"}}
    graph.invoke({"user_input": "清理一下磁盘"}, cfg)
    resumed = graph.invoke(Command(resume={"approved": False, "comment": "风险太大"}), cfg)
    assert resumed["approved"] is False
    assert "拒绝" in resumed["final_answer"]
