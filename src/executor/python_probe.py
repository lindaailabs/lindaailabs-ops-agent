"""Run trusted repository probes through the configured local executor."""
import json
import shlex
import sys
from pathlib import Path
from typing import Any, Dict

from src.executor.local_executor import LocalExecutor
from src.executor.registry import get_executor


def run_local_probe(script: Path, operation: str, state: dict) -> Dict[str, Any]:
    args = state.get("skill_args") or {}
    if args.get("host", "localhost") not in ("localhost", "127.0.0.1", "::1"):
        return {"returncode": 2, "error": "当前仅支持本机采集，尚未连接远程主机。"}
    if not isinstance(get_executor(), LocalExecutor):
        return {"returncode": 2, "error": "此采集器需要 LocalExecutor。"}
    if sys.platform not in ("linux", "darwin"):
        return {"returncode": 2, "error": "此采集器需要在 Linux 或 macOS 上运行。"}
    command = shlex.join([sys.executable, str(script), operation, json.dumps(args)])
    out = get_executor().run(command, timeout=30)
    if out.get("returncode") != 0:
        return out
    try:
        result = json.loads(out.get("stdout", ""))
        if not isinstance(result, dict):
            raise ValueError("probe result is not an object")
        return result
    except (ValueError, TypeError):
        return {"returncode": 1, "error": "采集结果格式无效，无法分析。"}
