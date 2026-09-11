"""执行后端抽象层。

Skill 的 execute(state) 通过 get_executor() 获取后端并调用 run()，
因此切换 local / ssh 对 Skill 代码零侵入。
"""
from .base import Executor
from .local_executor import LocalExecutor
from .registry import get_executor, set_executor

__all__ = ["Executor", "LocalExecutor", "get_executor", "set_executor"]
