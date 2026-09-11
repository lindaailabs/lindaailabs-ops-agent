"""把 Skill 执行结果转换成面向运维人员的自然语言摘要。"""
import re
from typing import Any, Dict, List, Optional
from src.graph.service_presentation import format_service_result


_PERCENT_RE = re.compile(r"^(\d{1,3})%$")
_JAVA_PS_RE = re.compile(
    r"^\s*(?P<pid>\d+)\s+(?P<ppid>\d+)\s+(?P<user>\S+)\s+"
    r"(?P<pmem>[\d.]+)\s+(?P<pcpu>[\d.]+)\s+(?P<rss>\d+)\s+"
    r"(?P<vsz>\d+)\s+(?P<etime>\S+)\s+(?P<comm>\S+)\s+(?P<command>.*)$"
)


def _result_status(result: Dict[str, Any]) -> int:
    value = result.get("returncode", 0)
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _text(value: Any) -> str:
    return str(value or "").strip()


def _format_check_disk_usage(result: Dict[str, Any]) -> Optional[str]:
    stdout = _text(result.get("stdout"))
    if not stdout:
        return None

    rows: List[Dict[str, str]] = []
    for raw_line in stdout.splitlines():
        tokens = raw_line.split()
        percent_index = next(
            (index for index, token in enumerate(tokens) if _PERCENT_RE.match(token)),
            None,
        )
        if percent_index is None or percent_index < 4 or percent_index + 1 >= len(tokens):
            continue
        filesystem = " ".join(tokens[: percent_index - 3])
        rows.append(
            {
                "filesystem": filesystem,
                "size": tokens[percent_index - 3],
                "used": tokens[percent_index - 2],
                "available": tokens[percent_index - 1],
                "percent": tokens[percent_index],
                "mountpoint": " ".join(tokens[percent_index + 1 :]),
            }
        )

    if not rows:
        return None

    rows.sort(key=lambda row: int(row["percent"][:-1]), reverse=True)
    highest = rows[0]
    highest_percent = int(highest["percent"][:-1])
    if highest_percent >= 90:
        conclusion = "存在严重告警，建议立即处理空间释放或扩容。"
    elif highest_percent >= 80:
        conclusion = "存在空间告警，建议尽快检查大文件、日志和容器缓存。"
    else:
        conclusion = "未发现超过 80% 告警阈值的挂载点。"

    host = _text(result.get("host"))
    lines = [
        f"磁盘使用率检查完成（目标主机：{host}）。" if host else "磁盘使用率检查完成。",
        f"共检查 {len(rows)} 个挂载点，当前最高使用率为 {highest['percent']}（{highest['mountpoint']}）。",
        "重点挂载点：",
    ]
    for row in rows[:5]:
        percent = int(row["percent"][:-1])
        marker = "告警" if percent >= 80 else "正常"
        lines.append(
            f"- {row['mountpoint']}：已用 {row['percent']}，已用 {row['used']}/{row['size']}，"
            f"可用 {row['available']}（{marker}）"
        )
    if len(rows) > 5:
        lines.append(f"- 其余 {len(rows) - 5} 个挂载点未在摘要中展开。")
    lines.append(f"结论：{conclusion}")
    return "\n".join(lines)


def _format_disk_cleanup(result: Dict[str, Any]) -> Optional[str]:
    if result.get("action") != "disk_cleanup(dry-run)":
        return None
    path = _text(result.get("path")) or "目标路径"
    if _result_status(result) != 0 or result.get("error"):
        detail = _text(result.get("error")) or _text(result.get("stderr"))
        suffix = f"原因：{detail}" if detail else "请检查目标路径和执行环境。"
        return f"清理前评估未完成：{path}。本次没有删除任何文件。{suffix}"

    usage = _text(result.get("stdout"))
    usage = " ".join(usage.split()) if usage else "未返回占用统计"
    return (
        f"清理前评估完成：{path} 当前占用约 {usage}。"
        "本次仅执行 dry-run，没有删除任何文件。"
    )


def _format_bytes(value: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f}{unit}" if amount < 10 and unit != "B" else f"{amount:.0f}{unit}"
        amount /= 1024
    return f"{value}B"


def _parse_free_line(line: str) -> Optional[Dict[str, int]]:
    tokens = line.split()
    if len(tokens) < 4 or tokens[0].rstrip(":") not in {"Mem", "Swap"}:
        return None
    try:
        return {
            "total": int(tokens[1]),
            "used": int(tokens[2]),
            "free": int(tokens[3]),
            "available": int(tokens[6]) if tokens[0].rstrip(":") == "Mem" and len(tokens) > 6 else int(tokens[3]),
        }
    except (TypeError, ValueError):
        return None


def _parse_java_processes(stdout: str) -> List[Dict[str, Any]]:
    processes: List[Dict[str, Any]] = []
    in_java_section = False
    for line in stdout.splitlines():
        if line.strip() == "__JAVA_PROCESSES__":
            in_java_section = True
            continue
        if not in_java_section:
            continue
        match = _JAVA_PS_RE.match(line)
        if not match:
            continue
        process = match.groupdict()
        process["rss_bytes"] = int(process.pop("rss")) * 1024
        process["vsz_bytes"] = int(process.pop("vsz")) * 1024
        process["pmem"] = float(process["pmem"])
        process["pcpu"] = float(process["pcpu"])
        processes.append(process)
    return processes


def _format_memory_java(result: Dict[str, Any]) -> Optional[str]:
    stdout = _text(result.get("stdout"))
    if not stdout:
        return None

    memory = None
    swap = None
    for line in stdout.splitlines():
        if line.startswith("Mem:"):
            memory = _parse_free_line(line)
        elif line.startswith("Swap:"):
            swap = _parse_free_line(line)
    processes = _parse_java_processes(stdout)
    host = _text(result.get("host")) or "localhost"
    lines = [f"内存与 Java 进程分析完成（目标主机：{host}）。"]

    if memory:
        used_percent = (memory["used"] / memory["total"] * 100) if memory["total"] else 0
        lines.append(
            f"系统内存：已用 {_format_bytes(memory['used'])} / {_format_bytes(memory['total'])} "
            f"（{used_percent:.1f}%），可用约 {_format_bytes(memory['available'])}。"
        )
        if used_percent >= 90:
            lines.append("结论：系统内存处于严重紧张状态，优先检查 Java 堆、堆外内存和缓存增长。")
        elif used_percent >= 80:
            lines.append("结论：系统内存偏高，建议结合 Java 进程明细安排进一步排查。")
        else:
            lines.append("结论：系统内存总体尚有余量。")

    if swap and swap["total"]:
        swap_percent = swap["used"] / swap["total"] * 100
        lines.append(
            f"Swap：已用 {_format_bytes(swap['used'])} / {_format_bytes(swap['total'])} "
            f"（{swap_percent:.1f}%）。"
        )
        if swap["used"] > 0:
            lines.append("建议：Swap 已有使用，需关注是否发生内存回收或 Java 进程抖动；不要只看堆使用率。")
    else:
        lines.append("Swap：未配置或未发现可用 Swap。")

    if not processes:
        lines.append("Java 进程：未发现匹配的 Java 进程，或当前用户无权读取进程列表。")
        return "\n".join(lines)

    rss_total = sum(process["rss_bytes"] for process in processes)
    lines.append(f"Java 进程：发现 {len(processes)} 个，RSS 合计约 {_format_bytes(rss_total)}。")
    lines.append("按进程 RSS 排序的重点进程：")
    for process in processes[:8]:
        command = process["command"].strip()
        if len(command) > 110:
            command = command[:107] + "..."
        lines.append(
            f"- PID {process['pid']}：RSS {_format_bytes(process['rss_bytes'])}，"
            f"占系统 {process['pmem']:.1f}% ，CPU {process['pcpu']:.1f}% ，"
            f"运行 {process['etime']}；{command}"
        )

    xmx_processes = [
        process for process in processes if re.search(r"(?:^|\s)-Xmx\S+", process["command"])
    ]
    if xmx_processes:
        lines.append("针对性建议：")
        lines.append("- 优先对照上述 Java 进程的 RSS 与 -Xmx：RSS 明显高于堆上限时，要检查 Metaspace、DirectBuffer、线程栈和 JNI/native 内存。")
    else:
        lines.append("针对性建议：未从启动参数看到 -Xmx，建议为每个 Java 服务明确配置堆上限，并结合容器/主机内存预留。")
    if any(process["pmem"] >= 20 for process in processes):
        lines.append("- 存在单个 Java 进程占用系统内存达到 20% 以上的情况，建议优先采集该 PID 的 GC、堆外内存和线程信息。")
    lines.append("- 当前检查只读，不会重启或修改 Java 进程；如需进一步处理，应先确认具体 PID、服务归属和变更窗口。")
    return "\n".join(lines)


def format_execution_result(skill_name: str, result: Dict[str, Any]) -> str:
    """生成稳定、可读的最终答复，不把内部结果字典直接展示给用户。"""
    status = _result_status(result)
    if status != 0:
        detail = _text(result.get("stderr")) or _text(result.get("error"))
        message = f"技能「{skill_name}」执行失败（退出码 {status}）。"
        return f"{message}\n原因：{detail}" if detail else message
    if result.get("probe") in {"list_java_services", "check_cpu_usage", "check_service_health"}:
        return format_service_result(result)

    if skill_name == "check_disk_usage":
        formatted = _format_check_disk_usage(result)
        if formatted:
            return formatted
    if skill_name == "disk_cleanup":
        formatted = _format_disk_cleanup(result)
        if formatted:
            return formatted
    if skill_name == "check_memory_usage":
        formatted = _format_memory_java(result)
        if formatted:
            return formatted

    stdout = _text(result.get("stdout"))
    if stdout:
        return f"技能「{skill_name}」执行完成：\n{stdout}"
    details = [
        f"{key}={value}"
        for key, value in result.items()
        if key not in {"stdout", "stderr", "returncode"} and value not in (None, "")
    ]
    if details:
        return f"技能「{skill_name}」执行完成。\n结果：" + "；".join(details)
    return f"技能「{skill_name}」执行完成。"
