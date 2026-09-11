"""Skill 加载器测试：扫描独立 skills 仓库，校验结构与风险等级。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.loader.skill_loader import SkillLoader  # noqa: E402

SKILLS_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "lindaailabs-skills")
)


def test_scan_finds_two_skills():
    skills = SkillLoader(SKILLS_DIR).scan()
    assert "check_disk_usage" in skills
    assert "disk_cleanup" in skills


def test_risk_levels():
    skills = SkillLoader(SKILLS_DIR).scan()
    assert skills["check_disk_usage"].risk == "low"
    assert skills["disk_cleanup"].risk == "high"


def test_execute_callable():
    skills = SkillLoader(SKILLS_DIR).scan()
    for s in skills.values():
        assert callable(s.execute)
