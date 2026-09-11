"""执行后端抽象基类。"""
from abc import ABC, abstractmethod
from typing import Any, Dict


class Executor(ABC):
    @abstractmethod
    def run(self, command: str, timeout: int = 30) -> Dict[str, Any]:
        """执行一条命令，返回包含 stdout/stderr/returncode 的字典。"""
        ...
