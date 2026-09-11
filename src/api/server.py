"""FastAPI 服务：/chat 发起任务，/approve 处理审批，/reload_skills 热加载。

Phase 1 审批为 http_mock：/chat 若进入中断（高危操作），返回 pending_approval 与
thread_id；客户端调用 /approve 携带决策，服务用 Command(resume=...) 恢复图执行。
Phase 2 将把 /approve 替换为飞书/钉钉回调（需验签）。
"""
import os
import sqlite3
import sys

from dotenv import load_dotenv
from fastapi import FastAPI
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

# 将项目根加入 sys.path，使 skills 内 `from src.executor import ...` 可解析
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.api.bootstrap import bootstrap  # noqa: E402

load_dotenv()

app = FastAPI(title="Ops Agent")

# 全局初始化（启动一次）
STATE = bootstrap()


@app.post("/chat")
def chat(payload: dict):
    thread_id = payload.get("thread_id", "default")
    config = {"configurable": {"thread_id": thread_id}}
    result = STATE["graph"].invoke({"user_input": payload["message"]}, config)

    # 判断是否处于中断（待审批）
    snapshot = STATE["graph"].get_state(config)
    if snapshot.next:  # 还有后续节点 -> 被 interrupt 挂起
        task = snapshot.tasks[0] if snapshot.tasks else None
        interrupt_val = task.interrupts[0].value if task and task.interrupts else None
        return {
            "status": "pending_approval",
            "thread_id": thread_id,
            "approval_request": interrupt_val,
        }
    return {"status": "done", "answer": result.get("final_answer")}


@app.post("/approve")
def approve(payload: dict):
    thread_id = payload["thread_id"]
    config = {"configurable": {"thread_id": thread_id}}
    decision = {
        "approved": bool(payload.get("approved", False)),
        "comment": payload.get("comment", ""),
    }
    result = STATE["graph"].invoke(Command(resume=decision), config)
    return {"status": "done", "answer": result.get("final_answer")}


@app.post("/reload_skills")
def reload_skills():
    STATE["skills"] = STATE["loader"].scan()
    STATE["graph"] = build_graph_with(STATE)
    return {"skills": list(STATE["skills"].keys())}


def build_graph_with(state: dict):
    from src.graph.workflow import build_graph

    return build_graph(
        state["skills"], state["router"], state["executor"], state["checkpointer"]
    )
