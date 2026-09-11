"""Human-readable summaries of structured Linux service observations."""
from datetime import datetime, timezone


def service_line(row):
    age = row.get("uptime_seconds", 0)
    rss = row.get("rss_bytes", 0) / 1024**2
    return (f"{row['service']}（PID {row['pid']}），运行 {age // 3600} 小时 "
            f"{age % 3600 // 60} 分钟，RSS {rss:.0f} MiB")


def ports_text(listeners):
    if listeners is None:
        return "端口信息不可读取（权限不足）"
    return "、".join(f"{ip}:{port}" for ip, port in listeners[:8]) or "未发现 TCP 监听端口"


def format_service_result(result):
    data = result["data"]
    probe = result["probe"]
    lines = []
    if probe == "list_java_services":
        lines.append(f"本机当前可见 Java 服务共 {data['total']} 个。")
        for row in data["services"]:
            lines.append("- " + service_line(row) + "；" + ports_text(row.get("listeners")))
            if row.get("jvm_options"):
                lines.append("  JVM：" + " ".join(row["jvm_options"]))
            if row.get("identity_warning"):
                lines.append("  启动身份信息读取受限。")
        if data["total"] > len(data["services"]):
            lines.append("仅展示前 50 个服务，可按 PID 或 JAR 筛选。")
        lines.append("服务身份来自 JAR/主类；未检查业务接口。")
    elif probe == "check_cpu_usage":
        busy = data["busy_percent"]
        wait = data["iowait_percent"]
        if busy >= 80:
            lines.append("本次采样 CPU 使用较高，优先检查下面的高占用进程。")
        elif wait >= 10:
            lines.append("本次采样 I/O wait 较高，建议继续检查磁盘和 I/O。")
        else:
            lines.append("本次采样未见明显 CPU 饱和。")
        loads = " / ".join(f"{n:.2f}" for n in data["load_average"])
        lines.append(f"逻辑 CPU {data['cpu_count']} 个；1/5/15 分钟负载 {loads}。")
        lines.append(f"采样约 {data['sample_seconds']:.1f} 秒：CPU 忙碌 {busy:.1f}%，"
                     f"I/O wait {wait:.1f}%，steal {data['steal_percent']:.1f}%。")
        lines.append("高占用进程（单核 100%，多线程进程可超过 100%）：")
        for row in data["processes"]:
            lines.append(f"- {row['service']}（PID {row['pid']}）：CPU {row['cpu_percent']:.1f}%")
        if data.get("target"):
            lines.append("指定目标匹配结果：")
            for row in data["selected"]:
                lines.append("- " + service_line(row) + f"，CPU {row['cpu_percent']:.1f}%")
            if not data["selected"]:
                lines.append("本次采样未匹配目标；可能已退出或无读取权限。")
            elif len(data["selected"]) > 1:
                lines.append("匹配多个实例，进一步检查请指定 PID。")
        lines.append("负载不是 CPU 使用率；单次采样不能判断持续异常或根因。")
    elif probe == "check_service_health":
        state = data["state"]
        if state == "ambiguous":
            lines.append("匹配多个 Java 实例，请指定 PID 后再检查：")
            lines.extend("- " + service_line(row) for row in data["candidates"])
        elif state == "not_found":
            lines.append("未找到匹配的可见 Java 进程，请核对 PID/JAR；权限不足也可能导致缺失。")
        elif state in ("changed", "exited"):
            lines.append("采集期间进程已变化或退出，需要重新检查。")
        else:
            row = data["service"]
            lines.append(service_line(row))
            lines.append("进程状态：" + row["status"] + "；" + ports_text(row.get("listeners")))
            for check in data["tcp_checks"]:
                lines.append(f"- TCP {check['address']}:{check['port']}："
                             + ("可连接" if check["connected"] else "连接失败或超时"))
            if data["omitted_ports"]:
                lines.append(f"还有 {data['omitted_ports']} 个端口未探测。")
            http = data.get("http")
            if http:
                code = http.get("status_code")
                status = http.get("application_status")
                lines.append(f"HTTP 检查：{code if code is not None else '连接/TLS 失败或超时'}"
                             + (f"，应用状态 {status}" if status else ""))
                if code is not None and 200 <= code < 300 and status == "UP":
                    lines.append("该健康接口本次返回 UP；仍不能代表全部业务链路。")
                elif code is not None and 200 <= code < 300 and not status:
                    lines.append("HTTP 请求成功，但响应未提供可识别的应用健康状态。")
                else:
                    lines.append("健康接口未通过检查，需结合服务日志进一步定位。")
            else:
                lines.append("未提供健康接口地址，尚未验证业务健康；TCP 可连接只说明端口可达。")
    if data.get("inaccessible_processes"):
        lines.append(f"有 {data['inaccessible_processes']} 个进程读取受限，清单可能不完整。")
    timestamp = result.get("checked_at")
    if timestamp:
        lines.append("采集时间：" + datetime.fromtimestamp(timestamp, timezone.utc).isoformat())
    lines.append("范围：本机当前可见的 PID/网络命名空间。")
    return "\n".join(lines)
