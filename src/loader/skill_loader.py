"""动态 Skill 加载。

- 扫描 skill_dir 下每个子目录；
- 解析 SKILL.md 的 YAML frontmatter（仅 yaml.safe_load，禁用 eval）；
- 通过 importlib 动态加载 executor.py（spec_from_file_location，避免 sys.modules 缓存干扰）；
- 校验必须暴露可调用的 execute(state)。
"""
import importlib.util
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import yaml


@dataclass
class Skill:
    name: str
    risk: str  # "low" | "high"
    use_case: str
    description: str
    trigger: str
    execute: Callable[[Dict[str, Any]], Dict[str, Any]]
    path: str
    required_args: List[str] = field(default_factory=list)  # 触发前必须补齐的参数


class SkillLoader:
    def __init__(self, skill_dir: str):
        self.skill_dir = skill_dir

    def scan(self) -> Dict[str, Skill]:
        skills: Dict[str, Skill] = {}
        if not os.path.isdir(self.skill_dir):
            return skills
        for entry in sorted(os.listdir(self.skill_dir)):
            skill_path = os.path.join(self.skill_dir, entry)
            if not os.path.isdir(skill_path):
                continue
            skill = self._load_one(skill_path)
            if skill is not None:
                skills[skill.name] = skill
        return skills

    def _load_one(self, skill_path: str) -> Optional[Skill]:
        md_path = os.path.join(skill_path, "SKILL.md")
        py_path = os.path.join(skill_path, "executor.py")
        if not (os.path.isfile(md_path) and os.path.isfile(py_path)):
            return None

        meta = self._parse_frontmatter(md_path)
        if not meta.get("name"):
            raise ValueError(f"{md_path} 缺少 name 字段")

        # importlib 动态加载（不污染 sys.modules）
        spec = importlib.util.spec_from_file_location(f"skill_{meta['name']}", py_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        execute = getattr(module, "execute", None)
        if not callable(execute):
            raise ValueError(f"Skill {meta['name']} 缺少可调用 execute()")

        return Skill(
            name=meta["name"],
            risk=str(meta.get("risk", "low")).lower(),
            use_case=meta.get("use_case", "simple_task"),
            description=meta.get("description", ""),
            trigger=meta.get("trigger", ""),
            execute=execute,
            path=skill_path,
            required_args=list(meta.get("required_args", []) or []),
        )

    @staticmethod
    def _parse_frontmatter(md_path: str) -> Dict[str, Any]:
        with open(md_path, "r", encoding="utf-8") as f:
            text = f.read()
        if not text.startswith("---"):
            raise ValueError(f"{md_path} 缺少 YAML frontmatter")
        end = text.find("---", 3)
        if end == -1:
            raise ValueError(f"{md_path} frontmatter 未闭合")
        return yaml.safe_load(text[3:end])
