"""多模型路由：从 settings.yaml 的模型池按 use_case 实例化 ChatModel。

凭证使用 ${ENV} 占位，由 os.path.expandvars 解析（配合 python-dotenv 加载 .env）。
禁止在代码里硬编码任何 api_key。
"""
import os
from typing import Any, Dict

import yaml
from langchain_openai import ChatOpenAI


class ModelRouter:
    def __init__(self, config_path: str = "config/settings.yaml"):
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        self._models: Dict[str, Dict[str, Any]] = {
            m["use_case"]: m for m in cfg.get("models", [])
        }
        if not self._models:
            raise ValueError("settings.yaml 未配置任何模型")

    def get(self, use_case: str = "simple_task", **kwargs: Any) -> ChatOpenAI:
        spec = self._models.get(use_case) or next(iter(self._models.values()))
        api_key = os.path.expandvars(spec["api_key"])  # 解析 ${OPENAI_API_KEY}
        return ChatOpenAI(
            model=spec["name"],
            api_key=api_key,
            base_url=spec.get("base_url"),
            **kwargs,
        )
