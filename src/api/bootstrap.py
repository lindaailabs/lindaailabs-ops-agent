"""服务启动引导：组装 loader / router / executor / checkpointer / graph。"""
import os
import sqlite3
from typing import Any, Dict

import yaml
from langgraph.checkpoint.sqlite import SqliteSaver

from src.executor import LocalExecutor
from src.executor.registry import set_executor
from src.graph.workflow import build_graph
from src.loader.skill_loader import SkillLoader
from src.models.router import ModelRouter


def bootstrap() -> Dict[str, Any]:
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "config", "settings.yaml"), "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    # 本地 Skill 目录优先级：环境变量 SKILL_DIR > settings.yaml 的 skill_dir > 默认 ./skills
    # 支持 ${ENV_VAR} 与 ~ 展开，便于跨环境（开发/生产）零改动切换。
    skill_dir = (
        os.environ.get("SKILL_DIR")
        or cfg.get("skill_dir")
        or "./skills"
    )
    skill_dir = os.path.expanduser(os.path.expandvars(skill_dir))
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
