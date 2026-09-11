from types import SimpleNamespace
import pytest
from fastapi import HTTPException

from src.api import server


class _FakeGraph:
    def invoke(self, *_args, **_kwargs):
        return {}

    def get_state(self, _config):
        return SimpleNamespace(
            next=("execute",),
            tasks=[
                SimpleNamespace(
                    interrupts=[
                        SimpleNamespace(
                            value={
                                "type": "approval_request",
                                "skill": "disk_cleanup",
                                "command": {"skill": "disk_cleanup", "args": {"path": "/data"}},
                            }
                        )
                    ]
                )
            ],
        )


def test_resume_reports_next_pending_interrupt(monkeypatch):
    monkeypatch.setitem(server.STATE, "graph", _FakeGraph())

    response = server.resume({"thread_id": "t1", "response": "/data"})

    assert response["status"] == "pending_approval"
    assert response["thread_id"] == "t1"
    assert response["approval_request"]["skill"] == "disk_cleanup"


def test_chat_reuses_explicit_thread_and_creates_isolated_default(monkeypatch):
    class Graph:
        def get_state(self, config):
            return SimpleNamespace(next=())
        def invoke(self, payload, config):
            return {"final_answer": "ok"}
    monkeypatch.setitem(server.STATE, "graph", Graph())
    a = server.chat({"message": "hello"})
    b = server.chat({"message": "hello"})
    assert a["thread_id"] != b["thread_id"]
    assert server.chat({"message": "continue", "thread_id": a["thread_id"]})["thread_id"] == a["thread_id"]


def test_chat_cannot_replace_a_pending_approval(monkeypatch):
    monkeypatch.setitem(server.STATE, "graph", _FakeGraph())
    with pytest.raises(HTTPException) as exc:
        server.chat({"message": "new instruction", "thread_id": "t1"})
    assert exc.value.status_code == 409
