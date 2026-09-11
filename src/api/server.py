"""FastAPI 服务：/chat 发起任务，/resume 通用恢复（澄清/审批），/approve 审批快捷入口，/reload_skills 热加载。

多轮交互：/chat 若进入中断，按 interrupt 类型返回 pending_clarification（缺参数，
需 /resume 带 response）或 pending_approval（高危，需 /resume 或 /approve 带决策）。
服务均用 Command(resume=...) 恢复图执行。Phase 2 将把 /approve 替换为飞书/钉钉回调（需验签）。
"""
import os
import sys
import uuid

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
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


def _format_graph_response(thread_id: str, config: dict, result: dict) -> dict:
    """Return a stable HTTP payload for completed or interrupted graph runs."""
    snapshot = STATE["graph"].get_state(config)
    if not snapshot.next:
        return {"status": "done", "thread_id": thread_id, "answer": result.get("final_answer")}

    task = snapshot.tasks[0] if snapshot.tasks else None
    interrupt_val = task.interrupts[0].value if task and task.interrupts else None
    itype = (interrupt_val or {}).get("type", "approval_request")
    if itype == "clarification_request":
        return {
            "status": "pending_clarification",
            "thread_id": thread_id,
            "clarification_request": interrupt_val,
        }
    return {
        "status": "pending_approval",
        "thread_id": thread_id,
        "approval_request": interrupt_val,
    }


@app.post("/chat")
def chat(payload: dict):
    thread_id = payload.get("thread_id") or uuid.uuid4().hex
    config = {"configurable": {"thread_id": thread_id}}
    if STATE["graph"].get_state(config).next:
        raise HTTPException(status_code=409, detail="本会话仍待恢复，请调用 /resume 或使用新的 thread_id。")
    result = STATE["graph"].invoke(
        {
            "user_input": payload["message"],
            # 人工对话默认 interactive（已在场=授权，不二次审批）；
            # 定时/自动触发请显式传 "mode": "automated" 以启用审批闸门
            "mode": payload.get("mode", "interactive"),
        },
        config,
    )
    return _format_graph_response(thread_id, config, result)


@app.post("/resume")
def resume(payload: dict):
    """通用恢复端点：处理澄清回复与审批决策两类 interrupt。

    - 澄清：payload = {"thread_id": "...", "response": "用户回复文本或 JSON 对象"}
    - 审批：payload = {"thread_id": "...", "response": {"approved": true, "comment": "..."}}
    """
    thread_id = payload["thread_id"]
    config = {"configurable": {"thread_id": thread_id}}
    result = STATE["graph"].invoke(Command(resume=payload.get("response")), config)
    return _format_graph_response(thread_id, config, result)


@app.post("/approve")
def approve(payload: dict):
    thread_id = payload["thread_id"]
    config = {"configurable": {"thread_id": thread_id}}
    decision = {
        "approved": bool(payload.get("approved", False)),
        "comment": payload.get("comment", ""),
    }
    result = STATE["graph"].invoke(Command(resume=decision), config)
    return _format_graph_response(thread_id, config, result)


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
