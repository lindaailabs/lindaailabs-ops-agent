from types import SimpleNamespace

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
