"""Bounded conversation history; execution fields belong to one turn only."""
from datetime import datetime, timezone
from typing import Any, Dict
from src.graph.state import OpsAgentState


def prepare_turn(state: OpsAgentState) -> Dict[str, Any]:
    return {
        "selected_skill": None, "skill_args": {}, "risk_level": "",
        "command": None, "execution_result": None, "approved": None,
        "approval_comment": None, "final_answer": None, "error": None,
        "clarification_messages": [],
    }


def remember_turn(state: OpsAgentState) -> Dict[str, Any]:
    answer = str(state.get("final_answer") or "本轮未返回结果。")
    history = list(state.get("messages") or [])
    history.append({"role": "user", "content": str(state["user_input"])[:4000]})
    history.extend(state.get("clarification_messages") or [])
    history.append({"role": "assistant", "content": answer[:6000]})
    observations = list(state.get("observations") or [])
    if state.get("execution_result") is not None:
        observations.append({
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "skill": state.get("selected_skill"),
            "args": state.get("skill_args"),
            "summary": answer[:6000],
        })
    return {"messages": history[-16:], "observations": observations[-3:]}
