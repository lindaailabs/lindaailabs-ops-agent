import sys
from types import SimpleNamespace

from src import cli


def test_cli_rejects_non_object_args(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["ops-agent", "--skill", "demo", "--args", "[]"])

    assert cli.main() == 2


def test_cli_rejects_missing_required_args(monkeypatch):
    skill = SimpleNamespace(
        name="disk_cleanup",
        risk="high",
        required_args=["path"],
        execute=lambda _state: {},
    )
    monkeypatch.setattr(cli, "bootstrap", lambda: {"skills": {"disk_cleanup": skill}})
    monkeypatch.setattr(sys, "argv", ["ops-agent", "--skill", "disk_cleanup"])

    assert cli.main() == 2
