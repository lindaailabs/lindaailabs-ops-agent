"""REPL 模式测试：不起 HTTP 端口，用 stdin/stdout 驱动同一套 graph 走 LLM 多轮。

复用 test_hitl 的 fake 构造方式（fake planner + fake router 抽参数），避免依赖真实 LLM key。
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch

from langgraph.checkpoint.sqlite import SqliteSaver

from src.executor import LocalExecutor, set_executor  # noqa: E402
from src.graph.workflow import build_graph  # noqa: E402
from src.graph.nodes import build_clarify_node, build_executor_node  # noqa: E402
from src.loader.skill_loader import Skill  # noqa: E402
from src import cli  # noqa: E402


def _build_graph(router):
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
    return build_graph(
        skills,
        router=router,
        executor=LocalExecutor(),
        checkpointer=saver,
        planner_node=fake_planner,
        clarify_node=build_clarify_node(skills, router),
        executor_node=build_executor_node(skills),
    )


class _FakeRouter:
    """模拟 simple_task 模型从自然语言抽取参数。"""

    def get(self, use_case="simple_task", **kwargs):
        class _LLM:
            def invoke(self, prompt):
                class _Msg:
                    content = '{"path": "/data"}'

                return _Msg()

        return _LLM()


def test_repl_clarify_then_done(capsys):
    """REPL：用户自然语言提问 -> 缺参数澄清 -> 自然语言补参 -> 执行完成。"""
    graph = _build_graph(_FakeRouter())
    state = {"graph": graph}
    with patch(
        "builtins.input",
        side_effect=["跑一下 fake_low", "就是 /data 那个盘", "exit"],
    ):
        rc = cli.run_repl(state, mode="interactive")
    assert rc == 0
    out = capsys.readouterr().out
    assert "/data" in out  # 自然语言被抽成 path=/data 并执行


def test_repl_no_match_fallback(capsys):
    """REPL：问法超出能力时，planner 预置自然语言回答而非死路。"""
    fake_skill = Skill(
        name="fake_low", risk="low", use_case="simple_task",
        description="demo", trigger="", execute=lambda s: {}, path="", required_args=[],
    )

    def fake_planner(state):
        return {
            "final_answer": "我目前能做：查磁盘、清磁盘。请说具体些。",
            "selected_skill": None,
        }

    set_executor(LocalExecutor())
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    saver = SqliteSaver(conn)
    graph = build_graph(
        {"fake_low": fake_skill}, router=None, executor=LocalExecutor(),
        checkpointer=saver, planner_node=fake_planner,
    )
    state = {"graph": graph}
    with patch(
        "builtins.input",
        side_effect=["你好，我该问什么", "exit"],
    ):
        rc = cli.run_repl(state, mode="interactive")
    assert rc == 0
    out = capsys.readouterr().out
    assert "查磁盘" in out


def _raise_eof(*_args, **_kwargs):
    raise EOFError


def test_cli_main_repl_flag(monkeypatch, capsys):
    """--repl 入口：main 应进入 REPL 而非要求 --skill。"""
    graph = _build_graph(_FakeRouter())
    monkeypatch.setattr(cli, "bootstrap", lambda: {"graph": graph})
    monkeypatch.setattr(
        "sys.argv",
        ["src/cli.py", "--repl", "--mode", "interactive"],
    )
    monkeypatch.setattr("builtins.input", _raise_eof)  # 立即 EOF 退出
    rc = cli.main()
    assert rc == 0
