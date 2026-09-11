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
from src.graph.nodes import build_clarify_node, build_executor_node  # noqa: E402
from src.loader.skill_loader import Skill, SkillLoader  # noqa: E402

SKILLS_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "lindaailabs-skills")
)


def _make_planner(skills, force_args=None):
    def planner(state):
        name = "disk_cleanup" if "清理" in state["user_input"] else "check_disk_usage"
        sk = skills[name]
        args = force_args if force_args is not None else state.get("skill_args") or {"host": "localhost"}
        return {
            "selected_skill": name,
            "skill_args": args,
            "risk_level": sk.risk,
            "command": {"skill": name, "args": {}},
        }

    return planner


def _build(planner_node=None):
    skills = SkillLoader(SKILLS_DIR).scan()
    set_executor(LocalExecutor())
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    saver = SqliteSaver(conn)
    graph = build_graph(
        skills,
        router=None,
        executor=LocalExecutor(),
        checkpointer=saver,
        planner_node=planner_node or _make_planner(skills),
    )
    return graph


def test_low_risk_runs_without_approval():
    graph = _build()
    cfg = {"configurable": {"thread_id": "t1"}}
    res = graph.invoke({"user_input": "查看磁盘使用率"}, cfg)
    assert res["approved"] is True
    assert res["final_answer"]


def test_high_risk_interrupt_then_approve():
    graph = _build(planner_node=_make_planner(SkillLoader(SKILLS_DIR).scan(), force_args={"path": "/tmp"}))
    cfg = {"configurable": {"thread_id": "t2"}}
    # planner 直接给出 path，跳过澄清，仅触发审批（自动触发模式才挂起）
    graph.invoke({"user_input": "清理一下磁盘", "mode": "automated"}, cfg)
    snap = graph.get_state(cfg)
    assert snap.next  # 被 interrupt 挂起

    resumed = graph.invoke(Command(resume={"approved": True, "comment": "同意"}), cfg)
    assert resumed["approved"] is True
    assert resumed["final_answer"]


def test_high_risk_reject():
    graph = _build(planner_node=_make_planner(SkillLoader(SKILLS_DIR).scan(), force_args={"path": "/tmp"}))
    cfg = {"configurable": {"thread_id": "t3"}}
    graph.invoke({"user_input": "清理一下磁盘", "mode": "automated"}, cfg)
    resumed = graph.invoke(Command(resume={"approved": False, "comment": "风险太大"}), cfg)
    assert resumed["approved"] is False
    assert "拒绝" in resumed["final_answer"]


def test_high_risk_clarify_then_approve():
    """多轮（自动触发）：先缺 path -> 澄清中断 -> 补 path -> 高危审批 -> 执行。"""
    graph = _build()  # planner 给空 args，不含 path
    cfg = {"configurable": {"thread_id": "t4"}}

    graph.invoke({"user_input": "清理一下磁盘", "mode": "automated"}, cfg)
    snap = graph.get_state(cfg)
    assert snap.next
    # 第一轮中断应为澄清（缺 path）
    val = snap.tasks[0].interrupts[0].value
    assert val["type"] == "clarification_request"
    assert "path" in val["missing"]

    # 用户回复路径 -> 恢复，应进入审批中断
    graph.invoke(Command(resume="/data"), cfg)
    snap2 = graph.get_state(cfg)
    assert snap2.next
    val2 = snap2.tasks[0].interrupts[0].value
    assert val2["type"] == "approval_request"

    # 审批通过 -> 执行
    resumed = graph.invoke(Command(resume={"approved": True, "comment": "同意"}), cfg)
    assert resumed["approved"] is True
    assert resumed["final_answer"]


def test_high_risk_interactive_runs_without_approval():
    """人工对话交互(interactive)中高危技能直接执行，不挂起审批。"""
    graph = _build(planner_node=_make_planner(SkillLoader(SKILLS_DIR).scan(), force_args={"path": "/tmp"}))
    cfg = {"configurable": {"thread_id": "t6"}}
    res = graph.invoke({"user_input": "清理一下磁盘", "mode": "interactive"}, cfg)
    snap = graph.get_state(cfg)
    assert not snap.next  # 未挂起，直接执行
    assert res["approved"] is True
    assert res["final_answer"]


def test_low_risk_clarification():
    """低风险技能带 required_args 时也应走澄清，且不触发审批。"""
    # 构造一个低风险、需要 path 的虚拟技能
    fake_skill = Skill(
        name="fake_low",
        risk="low",
        use_case="simple_task",
        description="low-risk demo with required arg",
        trigger="",
        execute=lambda s: {"echo": (s.get("skill_args") or {}).get("path")},
        path="",
        required_args=["path"],
    )
    skills = {"fake_low": fake_skill}

    def fake_planner(state):
        return {
            "selected_skill": "fake_low",
            "skill_args": {},
            "risk_level": "low",
            "command": {"skill": "fake_low", "args": {}},
        }

    set_executor(LocalExecutor())
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    saver = SqliteSaver(conn)
    graph = build_graph(
        skills,
        router=None,
        executor=LocalExecutor(),
        checkpointer=saver,
        planner_node=fake_planner,
        clarify_node=build_clarify_node(skills),
        executor_node=build_executor_node(skills),
    )
    cfg = {"configurable": {"thread_id": "t5"}}

    graph.invoke({"user_input": "跑一下 fake_low"}, cfg)
    snap = graph.get_state(cfg)
    assert snap.next
    val = snap.tasks[0].interrupts[0].value
    assert val["type"] == "clarification_request"

    # 回复后应是最终答案（无审批）
    resumed = graph.invoke(Command(resume={"path": "/var"}), cfg)
    assert resumed["approved"] is True
    assert resumed["execution_result"]["echo"] == "/var"
