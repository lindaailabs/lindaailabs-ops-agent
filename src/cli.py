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
from langgraph.types import Command  # noqa: E402

from src.api.bootstrap import bootstrap  # noqa: E402

try:  # langgraph 不同版本 interrupt 行为略有差异，做防御性兼容
    from langgraph.errors import GraphInterrupt
except Exception:  # pragma: no cover
    class GraphInterrupt(Exception):
        pass

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


def _drive_turn(graph, config, payload):
    """执行一步图调用，返回 (status, data)。

    status: 'done' | 'clarify' | 'approval'
    - done:     图已结束，data 为 final_answer
    - clarify:  缺参数被中断，data 为 clarification_request
    - approval: 高危待审批被中断，data 为 approval_request
    """
    try:
        graph.invoke(payload, config)
    except GraphInterrupt:
        pass
    snap = graph.get_state(config)
    if not snap.next:
        return "done", snap.values.get("final_answer")
    task = snap.tasks[0] if snap.tasks else None
    iv = task.interrupts[0].value if task and task.interrupts else None
    itype = (iv or {}).get("type", "approval_request")
    if itype == "clarification_request":
        return "clarify", iv
    return "approval", iv


def run_repl(state, mode: str = "interactive") -> int:
    """对话 REPL：不起 HTTP 端口，直接对接 LLM 走多轮。

    复用与 /chat 完全相同的 graph（planner + clarify 走 LLM + executor），
    仅以 stdin/stdout 驱动 Command(resume=...)。默认 interactive（人在场=授权，
    高危直执行不二次审批）；传 --mode automated 可演示审批闸门。
    """
    import uuid

    graph = state["graph"]
    print("=" * 54)
    print("Ops Agent 对话模式（不走 HTTP 端口，直接对接 LLM 多轮）")
    print(f"模式: {mode}   |   输入 exit / quit 退出")
    print("=" * 54)
    phase = "new"  # new（新话题）| mid（澄清/审批进行中）
    config = None
    while True:
        if phase == "new":
            config = {"configurable": {"thread_id": f"repl-{uuid.uuid4().hex[:8]}"}}
            prompt = "你> "
        else:
            prompt = ">> "

        try:
            line = input(prompt).strip()
        except EOFError:
            print("\n再见")
            break
        if line.lower() in ("exit", "quit"):
            print("再见")
            break
        if not line:
            continue

        if phase == "new":
            payload = {"user_input": line, "mode": mode}
        else:
            payload = Command(resume=line)  # 澄清补参数用自然语言文本

        status, data = _drive_turn(graph, config, payload)

        if status == "done":
            print("Agent>", data or "(无输出)")
            phase = "new"
        elif status == "clarify":
            msg = data.get("message") or data.get("clarification_request") or data
            print("Agent>", msg)
            phase = "mid"
        else:  # approval（仅 automated 模式会出现）
            ans = input("Agent> 高危操作需审批，是否批准? [y/N] ").strip().lower()
            status2, data2 = _drive_turn(
                graph, config, Command(resume={"approved": ans in ("y", "yes"), "comment": ans})
            )
            print("Agent>", data2 or "(无输出)")
            phase = "new"
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Ops Agent 手动触发 CLI")
    parser.add_argument("--skill", help="要触发的 Skill 名称（手动直跑模式，绕过 LLM）")
    parser.add_argument("--args", default="{}", help="Skill 参数 JSON，如 '{\"host\":\"s1\"}'")
    parser.add_argument("--yes", action="store_true", help="高危技能免交互直接批准（仅限可信环境）")
    parser.add_argument("--repl", action="store_true", help="进入对话 REPL（对接 LLM 多轮，不起 HTTP 端口）")
    parser.add_argument("--mode", default="interactive", choices=["interactive", "automated"],
                        help="REPL 模式：interactive(默认, 人在场直执行) / automated(启用审批闸门)")
    args = parser.parse_args()

    state = bootstrap()

    if args.repl:
        return run_repl(state, mode=args.mode)

    if not args.skill:
        parser.error("--skill 必填（或改用 --repl 进入对话模式）")
        return 2

    try:
        skill_args = json.loads(args.args)
    except json.JSONDecodeError as exc:
        print(f"[ERROR] --args 不是合法 JSON: {exc}")
        return 2
    if not isinstance(skill_args, dict):
        print("[ERROR] --args 必须是 JSON 对象，如 '{\"path\":\"/tmp\"}'")
        return 2

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
