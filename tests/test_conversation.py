import sqlite3

from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from src.graph.workflow import build_graph
from src.loader.skill_loader import Skill
from src.executor import LocalExecutor
from src import cli


class Router:
    def __init__(self, answers):
        self.answers = iter(answers)
        self.prompts = []
        self.tools = []

    def get(self, **kwargs):
        return self

    def bind(self, tools):
        self.tools = tools
        return self

    def invoke(self, prompt):
        self.prompts.append(prompt)
        return next(self.answers)


def call(name="demo", args=None):
    return AIMessage(content="", tool_calls=[{"id": "call1", "name": name, "args": args or {}}])


def make_graph(router, execute, risk="low", required=None):
    skill = Skill("demo", risk, "simple_task", "demo", "", execute, "",
                  required_args=required or [], parameters={
                      "target": {"type": "string", "description": "PID 或服务名"},
                      "health_url": {"type": "string", "description": "健康接口"},
                  })
    return build_graph({"demo": skill}, router, LocalExecutor(),
                       SqliteSaver(sqlite3.connect(":memory:", check_same_thread=False)))


def test_followup_retains_evidence_without_reexecuting_or_reusing_old_state():
    executions = []
    def execute(state):
        executions.append(dict(state["skill_args"]))
        return {"stdout": "order.jar PID 101 CPU 92%", "returncode": 0}
    router = Router([call(args={"target": "101"}), AIMessage(content="上次订单服务 CPU 为 92%。"),
                     call(args={"target": "202"})])
    graph = make_graph(router, execute)
    cfg = {"configurable": {"thread_id": "conversation"}}
    graph.invoke({"user_input": "查 CPU"}, cfg)
    answer = graph.invoke({"user_input": "它有问题吗"}, cfg)
    assert executions == [{"target": "101"}]
    assert answer["execution_result"] is None
    assert answer["selected_skill"] is None
    assert answer["skill_args"] == {}
    assert "92%" in answer["final_answer"]
    prompt = "\n".join(str(m.content) for m in router.prompts[1])
    assert "order.jar PID 101" in prompt and "checked_at" in prompt
    assert len(answer["observations"]) == 1
    graph.invoke({"user_input": "检查另外一个 202"}, cfg)
    assert executions[-1] == {"target": "202"}
    assert "health_url" in router.tools[0]["function"]["parameters"]["properties"]


def test_new_thread_has_no_previous_conversation():
    router = Router([call(), AIMessage(content="请指定服务。")])
    graph = make_graph(router, lambda _: {"stdout": "private-pid-101"})
    graph.invoke({"user_input": "检查"}, {"configurable": {"thread_id": "a"}})
    graph.invoke({"user_input": "它呢"}, {"configurable": {"thread_id": "b"}})
    assert all("private-pid-101" not in str(m.content) for m in router.prompts[1])


def test_failure_and_denial_do_not_reuse_previous_result_or_approval():
    calls = []
    def execute(_):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("probe unavailable")
        return {"stdout": "first success"}
    router = Router([call(), call(), call()])
    graph = make_graph(router, execute, risk="high")
    cfg = {"configurable": {"thread_id": "failures"}}
    graph.invoke({"user_input": "执行", "mode": "interactive"}, cfg)
    failed = graph.invoke({"user_input": "再执行", "mode": "interactive"}, cfg)
    assert "probe unavailable" in failed["final_answer"]
    graph.invoke({"user_input": "定时执行", "mode": "automated"}, cfg)
    assert graph.get_state(cfg).tasks[0].interrupts[0].value["type"] == "approval_request"
    rejected = graph.invoke(Command(resume={"approved": False}), cfg)
    assert "拒绝" in rejected["final_answer"]
    assert rejected["execution_result"] is None and rejected["error"] is None
    assert len(calls) == 2
    assert len(rejected["messages"]) == 6


def test_clarified_target_is_in_approval_and_history():
    router = Router([call()])
    graph = make_graph(router, lambda s: {"stdout": s["skill_args"]["target"]},
                       risk="high", required=["target"])
    cfg = {"configurable": {"thread_id": "clarify"}}
    graph.invoke({"user_input": "检查服务", "mode": "automated"}, cfg)
    graph.invoke(Command(resume={"target": "101"}), cfg)
    approval = graph.get_state(cfg).tasks[0].interrupts[0].value
    assert approval["command"]["args"]["target"] == "101"
    result = graph.invoke(Command(resume={"approved": True}), cfg)
    assert any("101" in m["content"] for m in result["messages"] if m["role"] == "user")


def test_repl_keeps_session_until_new(monkeypatch):
    seen = []
    def drive(_graph, cfg, payload):
        seen.append(cfg["configurable"]["thread_id"])
        return "done", "ok"
    monkeypatch.setattr(cli, "_drive_turn", drive)
    inputs = iter(["列服务", "它呢", "/new", "列服务", "exit"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    assert cli.run_repl({"graph": object()}) == 0
    assert seen[0] == seen[1] and seen[2] != seen[0]
