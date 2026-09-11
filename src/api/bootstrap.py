"""服务启动引导：组装 loader / router / executor / checkpointer / graph。"""
import os
import sqlite3
from typing import Any, Dict

import yaml

from src.executor import LocalExecutor
from src.executor.registry import set_executor
from src.graph.workflow import build_graph
from src.loader.skill_loader import SkillLoader
from src.models.router import ModelRouter


def bootstrap() -> Dict[str, Any]:
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "config", "settings.yaml"), "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    skill_dir = os.path.expandvars(cfg.get("skill_dir", "./skills"))
    loader = SkillLoader(skill_dir)
    skills = loader.scan()

    router = ModelRouter(os.path.join(root, "config", "settings.yaml"))
    executor = LocalExecutor()
    set_executor(executor)

    checkpoint = cfg.get("checkpoint", {})
    db_path = os.path.expandvars(checkpoint.get("path", "./data/checkpoints.db"))
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    saver = SqliteSaver(conn)

    graph = build_graph(skills, router, executor, saver)

    return {
        "loader": loader,
        "skills": skills,
        "router": router,
        "executor": executor,
        "checkpointer": saver,
        "graph": graph,
    }
