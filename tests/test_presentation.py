from src.graph.presentation import format_execution_result


def test_check_disk_usage_is_summarized_for_people():
    result = {
        "host": "localhost",
        "stdout": (
            "Filesystem Size Used Avail Use% Mounted on\n"
            "/dev/sda1 100G 91G 9G 91% /\n"
            "/dev/sdb1 200G 40G 160G 20% /data\n"
        ),
        "stderr": "",
        "returncode": 0,
    }

    answer = format_execution_result("check_disk_usage", result)

    assert "磁盘使用率检查完成" in answer
    assert "/：已用 91%" in answer
    assert "告警" in answer
    assert "{'host'" not in answer


def test_disk_cleanup_explains_dry_run_without_raw_dict():
    result = {
        "action": "disk_cleanup(dry-run)",
        "path": "/data",
        "stdout": "12G\t/data\n",
        "stderr": "",
        "returncode": 0,
    }

    answer = format_execution_result("disk_cleanup", result)

    assert "当前占用约 12G /data" in answer
    assert "没有删除任何文件" in answer
    assert "{'action'" not in answer


def test_failed_skill_result_is_readable():
    answer = format_execution_result(
        "check_disk_usage",
        {"stdout": "", "stderr": "df: command not found", "returncode": 127},
    )

    assert "执行失败" in answer
    assert "df: command not found" in answer


def test_memory_analysis_highlights_java_processes_and_jvm_limits():
    result = {
        "skill": "check_memory_usage",
        "host": "app-01",
        "stdout": (
            "              total        used        free      shared  buff/cache   available\n"
            "Mem:     17179869184  15032385536  2147483648    104857600  1073741824  4294967296\n"
            "Swap:     4294967296   536870912  3758096384\n"
            "\n__JAVA_PROCESSES__\n"
            "  101  1 app  35.0  12.0  6291456  8388608  2-04:00:00 java java -Xms4g -Xmx8g -jar order.jar\n"
            "  202  1 app  12.0   3.0  2097152  4194304  1-02:00:00 java java -Xmx4g -jar pay.jar\n"
        ),
        "returncode": 0,
    }

    answer = format_execution_result("check_memory_usage", result)

    assert "目标主机：app-01" in answer
    assert "发现 2 个" in answer
    assert "PID 101" in answer
    assert "RSS" in answer
    assert "-Xmx" in answer
    assert "Swap 已有使用" in answer
    assert "{'skill'" not in answer
