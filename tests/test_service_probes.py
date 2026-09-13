import importlib.util
import json
import subprocess
import sys
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.graph.presentation import format_execution_result
from src.executor import LocalExecutor, set_executor
from src.executor.python_probe import run_local_probe


PROBE = Path(__file__).resolve().parents[2] / "lindaailabs-skills/_shared/linux_probe.py"
spec = importlib.util.spec_from_file_location("linux_probe_test", PROBE)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def row(pid=101, name="order.jar"):
    return {"pid": pid, "service": name, "started_at": 100.0, "uptime_seconds": 120,
            "rss_bytes": 1024**3, "status": "sleeping", "listeners": []}


def test_java_identity_does_not_expose_credentials_or_application_arguments():
    identity = probe.java_identity([
        "/opt/java/bin/java", "-Xmx2g", "-Ddb.password=secret", "-cp", "/app/lib/*",
        "-jar", "/app/order.jar", "--password=another-secret", "-Xmx999g",
    ])
    assert identity["service"] == "order.jar"
    assert identity["jvm_options"] == ["-Xmx2g"]
    assert "secret" not in json.dumps(identity)
    assert probe.java_identity(["java", "-cp", "lib/*", "com.app.Main"])["main_class"] == "com.app.Main"


def test_health_ambiguous_target_does_not_probe_any_port(monkeypatch):
    monkeypatch.setattr(probe, "inventory", lambda: {
        "services": [row(101), row(102)], "inaccessible_processes": 0})
    connect = Mock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(probe.socket, "create_connection", connect)
    result = probe.service_health("order")
    assert result["state"] == "ambiguous"
    assert len(result["candidates"]) == 2
    connect.assert_not_called()


def test_missing_permission_and_disappearing_process_are_not_healthy(monkeypatch):
    denied = Mock()
    denied.name.side_effect = probe.psutil.AccessDenied(1)
    vanished = Mock()
    vanished.name.side_effect = probe.psutil.NoSuchProcess(2)
    monkeypatch.setattr(probe.psutil, "process_iter", lambda: [denied, vanished])
    result = probe.service_health("order")
    assert result["state"] == "not_found"
    assert result["inaccessible_processes"] == 1


@pytest.mark.parametrize("url", [
    "http://example.com:8080/health", "http://127.0.0.1:9999/health",
    "http://user:pass@127.0.0.1:8080/health", "file:///etc/passwd",
    "http://127.0.0.1:8080/health?token=secret",
])
def test_health_url_must_belong_to_selected_process(url):
    with pytest.raises(ValueError):
        probe.validate_health_url(url, [("127.0.0.1", 8080)])


def test_ipv4_ipv6_and_wildcard_listener_validation():
    assert probe.validate_health_url("http://localhost:8080/health",
                                    [("0.0.0.0", 8080)]) == "http://127.0.0.1:8080/health"
    assert probe.validate_health_url("http://[::1]:8080/health",
                                    [("::", 8080)]) == "http://[::1]:8080/health"


@contextmanager
def health_server():
    hits = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/should-not-follow")
                self.end_headers()
                return
            code = 503 if self.path == "/down" else 200
            self.send_response(code)
            self.end_headers()
            self.wfile.write(b'{"status":"UP"}' if self.path == "/up" else b'{"status":"DOWN"}')

        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], hits
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_real_loopback_health_checks_tcp_http_and_does_not_follow_redirect(monkeypatch):
    with health_server() as (port, hits):
        service = row()
        service["listeners"] = [("127.0.0.1", port)]
        monkeypatch.setattr(probe, "inventory", lambda: {
            "services": [service], "inaccessible_processes": 0})
        monkeypatch.setattr(probe.psutil, "Process", lambda _: SimpleNamespace(
            is_running=lambda: True, create_time=lambda: 100.0))
        base = f"http://127.0.0.1:{port}"
        data = probe.service_health("101", base + "/up")
        assert data["tcp_checks"][0]["connected"]
        assert data["http"]["application_status"] == "UP"
        assert probe.http_health(base + "/redirect")["status_code"] == 302
        assert "/should-not-follow" not in hits
        down = probe.service_health("101", base + "/down")
        answer = format_execution_result("check_service_health", {
            "probe": "check_service_health", "data": down, "returncode": 0})
        assert "未通过" in answer and "503" in answer
        plain = probe.service_health("101")
        answer = format_execution_result("check_service_health", {
            "probe": "check_service_health", "data": plain, "returncode": 0})
        assert "尚未验证业务健康" in answer


def test_cpu_ranking_is_sampled_and_retains_service_identity(monkeypatch):
    procs = []
    for pid, cpu in [(101, 12.0), (102, 160.0)]:
        proc = Mock()
        proc.pid = pid
        proc.cpu_percent.side_effect = [0.0, cpu]
        proc.create_time.return_value = 100.0
        proc.is_running.return_value = True
        procs.append(proc)
    monkeypatch.setattr(probe.psutil, "process_iter", lambda: procs)
    monkeypatch.setattr(probe, "describe", lambda p: row(p.pid, f"svc-{p.pid}.jar"))
    times = Mock(return_value=SimpleNamespace(idle=10.0, iowait=5.0, steal=1.0))
    monkeypatch.setattr(probe.psutil, "cpu_times_percent", times)
    monkeypatch.setattr(probe.os, "getloadavg", lambda: (1.0, 2.0, 3.0), raising=False)
    result = probe.cpu_sample("102")
    assert result["processes"][0]["pid"] == 102
    assert result["selected"][0]["service"] == "svc-102.jar"
    assert result["busy_percent"] == 85.0
    times.assert_called_once_with(interval=1.0)


def test_probe_rejects_remote_host_without_execution():
    executor = LocalExecutor()
    executor.run = Mock(side_effect=AssertionError("must not execute"))
    set_executor(executor)
    result = run_local_probe(PROBE, "list_java_services", {"skill_args": {"host": "server-2"}})
    assert result["returncode"] != 0
    executor.run.assert_not_called()


@pytest.mark.skipif(sys.platform not in ("linux", "darwin"), reason="Linux/macOS live collection")
@pytest.mark.parametrize("operation", [
    "list_java_services", "check_cpu_usage", "check_memory_usage", "check_disk_usage"])
def test_linux_live_collection(operation):
    completed = subprocess.run([sys.executable, str(PROBE), operation, "{}"],
                               capture_output=True, text=True, timeout=30, check=True)
    result = json.loads(completed.stdout)
    assert result["returncode"] == 0
    assert result["probe"] == operation
    assert format_execution_result(operation, result)
