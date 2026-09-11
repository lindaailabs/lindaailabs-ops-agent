"""Skill 加载器测试：扫描独立 skills 仓库，校验结构与风险等级。"""
import os
import sys
import textwrap

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


def test_required_args_parsed():
    skills = SkillLoader(SKILLS_DIR).scan()
    assert skills["check_disk_usage"].required_args == []
    assert skills["disk_cleanup"].required_args == ["path"]


def test_execute_callable():
    skills = SkillLoader(SKILLS_DIR).scan()
    for s in skills.values():
        assert callable(s.execute)


def test_invalid_risk_fails_fast(tmp_path):
    skill_dir = tmp_path / "bad_skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: bad_skill
            risk: medium
            use_case: simple_task
            description: bad
            trigger: bad
            ---
            # bad
            """
        ),
        encoding="utf-8",
    )
    (skill_dir / "executor.py").write_text(
        "def execute(state):\n    return {}\n", encoding="utf-8"
    )

    try:
        SkillLoader(str(tmp_path)).scan()
    except ValueError as exc:
        assert "risk 必须是 low 或 high" in str(exc)
    else:
        raise AssertionError("invalid risk should fail")


def test_required_args_must_be_string_list(tmp_path):
    skill_dir = tmp_path / "bad_skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: bad_skill
            risk: low
            use_case: simple_task
            description: bad
            trigger: bad
            required_args: path
            ---
            # bad
            """
        ),
        encoding="utf-8",
    )
    (skill_dir / "executor.py").write_text(
        "def execute(state):\n    return {}\n", encoding="utf-8"
    )

    try:
        SkillLoader(str(tmp_path)).scan()
    except ValueError as exc:
        assert "required_args 必须是字符串列表" in str(exc)
    else:
        raise AssertionError("invalid required_args should fail")
