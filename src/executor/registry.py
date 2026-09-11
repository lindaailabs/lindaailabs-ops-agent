"""执行后端注册表（进程内单例）。"""
from typing import Optional

from .base import Executor

_current: Optional[Executor] = None


def set_executor(executor: Executor) -> None:
    global _current
    _current = executor


def get_executor() -> Executor:
    if _current is None:
        raise RuntimeError("Executor 尚未初始化，请先调用 set_executor()")
    return _current
