"""手动触发入口：绕过 LLM 规划器，直接调用指定 Skill。

用途：
- 不依赖 OPENAI_API_KEY，运维可手动/定时直接触发某个已知 Skill；
- high-risk 技能默认需显式 --yes 或在终端交互确认，复用 HITL 语义。

示例：
    python -m src.cli --skill check_disk_usage
    python -m src.cli --skill disk_cleanup --args '{"path":"/tmp"}' --yes
"""
import argparse
import json
import os
import sys

# 将项目根加入 sys.path，使 skills 内 `from src.executor import ...` 可解析
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dotenv import load_dotenv  # noqa: E402

from src.api.bootstrap import bootstrap  # noqa: E402

load_dotenv()


def _confirm_high_risk(skill_name: str, args: dict, auto_yes: bool) -> bool:
    print(f"[HITL] 即将执行高危技能: {skill_name}")
    print(f"      参数: {json.dumps(args, ensure_ascii=False)}")
    if auto_yes:
        print("[HITL] 检测到 --yes，自动批准。")
        return True
    try:
        ans = input("是否确认执行? [y/N] ").strip().lower()
    except EOFError:
        ans = ""
    return ans in ("y", "yes")


def main() -> int:
    parser = argparse.ArgumentParser(description="Ops Agent 手动触发 CLI")
    parser.add_argument("--skill", required=True, help="要触发的 Skill 名称")
    parser.add_argument("--args", default="{}", help="Skill 参数 JSON，如 '{\"host\":\"s1\"}'")
    parser.add_argument("--yes", action="store_true", help="高危技能免交互直接批准（仅限可信环境）")
    args = parser.parse_args()

    try:
        skill_args = json.loads(args.args)
    except json.JSONDecodeError as exc:
        print(f"[ERROR] --args 不是合法 JSON: {exc}")
        return 2
    if not isinstance(skill_args, dict):
        print("[ERROR] --args 必须是 JSON 对象，如 '{\"path\":\"/tmp\"}'")
        return 2

    state = bootstrap()
    skills = state["skills"]
    if args.skill not in skills:
        print(f"[ERROR] 未知 Skill: {args.skill}")
        print(f"        可用: {', '.join(sorted(skills.keys())) or '(无)'}")
        return 1

    skill = skills[args.skill]
    missing = [name for name in skill.required_args if not skill_args.get(name)]
    if missing:
        print(f"[ERROR] Skill {skill.name} 缺少必填参数: {', '.join(missing)}")
        print(f"        请用 --args 传入，例如: --args '{{\"{missing[0]}\":\"/tmp\"}}'")
        return 2

    if skill.risk == "high":
        if not _confirm_high_risk(skill.name, skill_args, auto_yes=args.yes):
            print("[ABORT] 用户拒绝，未执行。")
            return 0

    print(f"[RUN] 执行 Skill: {skill.name} (risk={skill.risk})")
    result = skill.execute({"skill_args": skill_args})
    print("[RESULT]")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
