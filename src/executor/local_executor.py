"""本地执行后端（subprocess）。Phase 1 默认实现，ssh 实现后续扩展。"""
import subprocess
from typing import Any, Dict

from .base import Executor


class LocalExecutor(Executor):
    def run(self, command: str, timeout: int = 30) -> Dict[str, Any]:
        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",  # 防止非 UTF-8 输出（如 GBK）导致解码异常
                timeout=timeout,
            )
            return {
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "returncode": proc.returncode,
            }
        except Exception as exc:  # 超时 / OSError 等
            return {"stdout": "", "stderr": str(exc), "returncode": -1}
