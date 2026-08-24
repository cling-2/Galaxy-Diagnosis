"""诊断信息采集 Tool 集（REQ-C-01）

4 个只读采集 Tool，按 env_type + container_runtime 差异化选用命令：
- collect_component_status  银河平台组件部署状态
- collect_service_logs      相关服务日志
- collect_system_resources  系统资源使用
- collect_network_connectivity  网络连通性

设计要点：
- 每个 Tool 是 Agent 唯一调用入口。当前以普通函数实现；后续引入 LangChain 时
  在此处为每个函数追加 ``@tool`` 装饰器即可，函数签名与返回结构不变（零返工）。
- 全部为只读操作，不调用任何写命令（set/config/mod/restart）。
- 工具缺失/权限不足时降级：抛 CollectorError 家族异常，由 context._safe_collect
  捕获并记 warning，不阻断其他 Tool。
- 容器 UNKNOWN 运行时：双路尝试（Docker + K8s），各自降级。

对齐 Diagnostic_collection_design.md §采集内容设计 §按环境差异化采集策略。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import TYPE_CHECKING

from galaxy_diag.shared.errors import (
    CollectorPermissionError,
    CollectorToolNotFoundError,
)
from galaxy_diag.shared.types import (
    ContainerRuntime,
    EnvironmentType,
    LogSnippet,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


# ===== 模块级工具函数（便于按模块路径 patch 测试） =====


def _read_file(path: str, max_bytes: int = 65536) -> str | None:
    """读取文件内容（最多 max_bytes 字节），失败返回 None

    安全防护：跳过符号链接到 /dev/* 的特殊文件（如 Docker 容器中
    /var/log/nginx/error.log → /dev/stderr）。这类文件 open() 读模式会
    阻塞等待数据（管道读端无写入者时永久挂起），是容器环境下采集卡死的常见根因。
    """
    try:
        # 解析符号链接后，若指向 /dev/（stdout/stderr/stdin 等设备/管道），跳过
        real = os.path.realpath(path)
        if real.startswith("/dev/"):
            return None
        # 仅读常规文件；FIFO/字符设备/套接字等特殊文件 open 可能阻塞，一并跳过
        import stat as _stat
        mode = os.stat(real).st_mode
        if not _stat.S_ISREG(mode):
            return None
        with open(real, encoding="utf-8", errors="replace") as f:
            return f.read(max_bytes)
    except (OSError, UnicodeDecodeError):
        return None


def _run_cmd(args: list[str], timeout: int = 10) -> tuple[int, str, str]:
    """执行命令返回 (returncode, stdout, stderr)

    所有 subprocess 调用的唯一出口，便于统一 patch。

    Raises:
        CollectorToolNotFoundError: 命令不存在
        CollectorPermissionError: 权限不足
    """
    cmd_name = args[0]
    if shutil.which(cmd_name) is None:
        raise CollectorToolNotFoundError(
            f"{cmd_name} 未安装",
            hint=f"请安装 {cmd_name}，或确认当前环境类型采集策略",
        )
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired as e:
        return 124, "", f"timeout: {e}"
    except PermissionError as e:
        raise CollectorPermissionError(
            f"执行 {cmd_name} 权限不足",
            hint="请以 root 运行或确认命令可执行权限",
        ) from e


# ===== Tool 1: 组件部署状态 =====


def collect_component_status(
    env_type: EnvironmentType,
    container_runtime: ContainerRuntime | None,
    components: "Sequence[str]",
) -> list[dict]:
    """采集银河平台组件部署状态

    Args:
        env_type: 环境类型
        container_runtime: 容器运行时子类型（CONTAINER 时有效）
        components: 组件名清单（GALAXY_COMPONENTS）

    Returns:
        组件状态列表 [{name, status, detail}]
        status 取值: running / failed / inactive / unknown
    """
    if env_type == EnvironmentType.CONTAINER:
        if container_runtime == ContainerRuntime.KUBERNETES:
            return _collect_component_status_k8s(components)
        if container_runtime == ContainerRuntime.DOCKER:
            return _collect_component_status_docker(components)
        # UNKNOWN：双路尝试
        warnings_tried: list[str] = []
        for fn, label in (
            (_collect_component_status_k8s, "kubectl"),
            (_collect_component_status_docker, "docker"),
        ):
            try:
                result = fn(components)
                if result:
                    return result
            except CollectorToolNotFoundError:
                warnings_tried.append(label)
        raise CollectorToolNotFoundError(
            "kubectl 与 docker 均不可用，无法采集容器组件状态",
            hint="容器运行时未确定，请安装 kubectl 或 docker",
        )

    # 裸金属 / VM：systemctl
    return _collect_component_status_systemctl(components)


def _collect_component_status_systemctl(components: "Sequence[str]") -> list[dict]:
    rc, stdout, _ = _run_cmd(
        ["systemctl", "is-active", *components]
    )
    # systemctl is-active 每行一个状态，rc 非 0 表示存在非 active 项
    lines = stdout.splitlines()
    results: list[dict] = []
    for name, line in zip(components, lines):
        state = line.strip().lower()
        status = _map_systemctl_state(state)
        results.append({"name": name, "status": status, "detail": f"systemctl: {state}"})
    # 若行数不足（部分组件不存在），补 unknown
    for name in components[len(lines):]:
        results.append({"name": name, "status": "unknown", "detail": "systemctl 无输出"})
    return results


def _collect_component_status_k8s(components: "Sequence[str]") -> list[dict]:
    rc, stdout, stderr = _run_cmd(
        ["kubectl", "get", "pods", "-A", "-o", "wide"]
    )
    if rc != 0:
        # 集群不可达
        raise CollectorToolNotFoundError(
            "kubectl 无法连接集群",
            hint=f"请确认 kubeconfig 已配置。stderr: {stderr.strip()[:200]}",
        )
    results: list[dict] = []
    lower_stdout = stdout.lower()
    for comp in components:
        if comp in lower_stdout:
            results.append({
                "name": comp, "status": "running",
                "detail": "kubectl: pod 存在",
            })
        else:
            results.append({
                "name": comp, "status": "inactive",
                "detail": "kubectl: 未发现对应 pod",
            })
    return results


def _collect_component_status_docker(components: "Sequence[str]") -> list[dict]:
    """Docker 环境组件状态采集

    优先用 docker ps（宿主机或挂载了 docker.sock 的容器）。
    docker CLI 不可用时（典型场景：容器内部未挂载 docker.sock），
    回退到进程树检测（/proc + ps），这是容器内部可靠的方式。
    """
    try:
        rc, stdout, _ = _run_cmd(["docker", "ps", "-a", "--format", "{{.Names}}\\t{{.Status}}"])
        if rc != 0:
            raise CollectorToolNotFoundError(
                "docker ps 失败",
                hint="请确认 docker daemon 运行中",
            )
    except CollectorToolNotFoundError:
        # 容器内部无 docker CLI / docker.sock → 回退到进程树检测
        return _collect_component_status_proc(components)
    # 构建容器名→状态映射
    container_map: dict[str, str] = {}
    for line in stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            container_map[parts[0].lower()] = parts[1]
    results: list[dict] = []
    lower_keys = " ".join(container_map).lower()
    for comp in components:
        matched = [v for k, v in container_map.items() if comp in k]
        if matched:
            status = "running" if "up" in matched[0].lower() else "failed"
            results.append({"name": comp, "status": status, "detail": f"docker: {matched[0]}"})
        elif comp in lower_keys:
            results.append({"name": comp, "status": "running", "detail": "docker: 命名匹配"})
        else:
            results.append({"name": comp, "status": "inactive", "detail": "docker: 未发现容器"})
    return results


def _collect_component_status_proc(components: "Sequence[str]") -> list[dict]:
    """进程树检测组件状态（容器内部回退方案）

    扫描 /proc/<pid>/cmdline（优先）或 ps aux 输出，匹配 GALAXY_COMPONENTS 中的进程名。
    这是 Docker 容器内部无 docker CLI 时的可靠检测方式。

    Raises:
        CollectorToolNotFoundError: ps 不可用且 /proc 不可读
    """
    # 优先读 /proc/<pid>/cmdline（无需 ps 命令，容器内必有 /proc）
    proc_lines: list[str] = []
    try:
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            cmdline_path = f"/proc/{entry}/cmdline"
            try:
                with open(cmdline_path, "r", errors="replace") as f:
                    # /proc cmdline 用 \0 分隔参数
                    cmdline = f.read().replace("\x00", " ").strip()
                if cmdline:
                    proc_lines.append(cmdline)
            except (OSError, PermissionError):
                continue
    except (OSError, PermissionError):
        proc_lines = []

    # /proc 不可读时回退到 ps aux
    if not proc_lines:
        try:
            rc, stdout, _ = _run_cmd(["ps", "aux"])
            if rc == 0:
                proc_lines = stdout.splitlines()
        except CollectorToolNotFoundError:
            raise CollectorToolNotFoundError(
                "ps 不可用且 /proc 不可读",
                hint="容器内无法检测进程，请确认 /proc 挂载",
            )

    # 合并所有进程命令行用于匹配
    all_procs_lower = "\n".join(proc_lines).lower()
    results: list[dict] = []
    for comp in components:
        comp_lower = comp.lower()
        # 匹配包含组件名的进程
        matching = [p for p in proc_lines if comp_lower in p.lower()]
        if matching:
            # 取第一个匹配进程的摘要作为详情
            detail_proc = matching[0][:80]
            results.append({
                "name": comp, "status": "running",
                "detail": f"proc: {detail_proc}",
            })
        else:
            results.append({
                "name": comp, "status": "inactive",
                "detail": "proc: 未发现进程",
            })
    return results


def _map_systemctl_state(state: str) -> str:
    """systemctl is-active 状态 → 统一状态"""
    if state in ("active", "activating"):
        return "running"
    if state in ("failed",):
        return "failed"
    if state in ("inactive", "deactivating"):
        return "inactive"
    return "unknown"


# ===== Tool 2: 服务日志 =====


def collect_service_logs(
    env_type: EnvironmentType,
    container_runtime: ContainerRuntime | None,
    log_paths: dict[str, str],
    keywords: "Sequence[str]",
) -> list[LogSnippet]:
    """采集相关服务日志

    Args:
        env_type: 环境类型
        container_runtime: 容器运行时子类型
        log_paths: 日志名→路径映射（KEY_LOG_PATHS）
        keywords: 过滤关键词（来自问题描述 + ERROR/Warning）

    Returns:
        LogSnippet 列表（已按关键词过滤，未截断——截断由 context.preprocess_logs 完成）
    """
    snippets: list[LogSnippet] = []
    filter_keywords = set(keywords) | {"error", "warning", "fail", "panic", "fatal"}

    if env_type == EnvironmentType.CONTAINER:
        if container_runtime == ContainerRuntime.KUBERNETES:
            snippets.extend(_collect_logs_k8s(log_paths, filter_keywords))
        elif container_runtime == ContainerRuntime.DOCKER:
            snippets.extend(_collect_logs_docker(log_paths, filter_keywords))
        else:
            # UNKNOWN：双路
            for fn in (_collect_logs_k8s, _collect_logs_docker):
                try:
                    snippets.extend(fn(log_paths, filter_keywords))
                except CollectorToolNotFoundError:
                    continue
            # 也尝试读容器内可见的日志文件
            snippets.extend(_collect_logs_from_files(log_paths, filter_keywords))
    else:
        # 裸金属 / VM：文件 + journalctl
        snippets.extend(_collect_logs_from_files(log_paths, filter_keywords))
        snippets.extend(_collect_logs_journalctl(log_paths, filter_keywords))

    return snippets


def _collect_logs_from_files(
    log_paths: dict[str, str],
    keywords: "set[str]",
) -> list[LogSnippet]:
    """从文件路径读取日志（尾部），按关键词过滤"""
    snippets: list[LogSnippet] = []
    for name, path in log_paths.items():
        content = _read_file(path)
        if content is None:
            continue
        filtered, level = _filter_log_lines(content, keywords)
        if not filtered:
            continue
        snippets.append(LogSnippet(
            source=path,
            level=level,
            timestamp="",
            content=filtered,
        ))
    return snippets


def _collect_logs_journalctl(
    log_paths: dict[str, str],
    keywords: "set[str]",
) -> list[LogSnippet]:
    """通过 journalctl 采集服务日志（裸金属/VM）"""
    snippets: list[LogSnippet] = []
    # 尝试为每个组件名查 journalctl
    for name in log_paths:
        try:
            rc, stdout, stderr = _run_cmd(
                ["journalctl", "-u", name, "--no-pager", "-n", "200"]
            )
        except CollectorToolNotFoundError:
            break  # journalctl 不可用，整体跳过
        if rc != 0 or not stdout.strip():
            continue
        filtered, level = _filter_log_lines(stdout, keywords)
        if not filtered:
            continue
        snippets.append(LogSnippet(
            source=f"journalctl:{name}",
            level=level,
            timestamp="",
            content=filtered,
        ))
    return snippets


def _collect_logs_k8s(
    log_paths: dict[str, str],
    keywords: "set[str]",
) -> list[LogSnippet]:
    """通过 kubectl logs 采集（K8s）"""
    snippets: list[LogSnippet] = []
    # kubelet 日志文件优先
    file_snippets = _collect_logs_from_files(
        {k: v for k, v in log_paths.items() if k in ("kubelet",)},
        keywords,
    )
    snippets.extend(file_snippets)
    # 尝试 kubectl logs（需 pod 名，此处采 kube-system 下所有 pod 的最近日志）
    try:
        rc, stdout, stderr = _run_cmd(
            ["kubectl", "logs", "-n", "kube-system", "-l", "k8s-app=kubelet", "--tail=100"]
        )
    except CollectorToolNotFoundError:
        # kubectl 不可用（典型：K8s Pod 内未挂载 kubectl），
        # 已有文件日志则返回，不抛"kubectl 未安装"硬错误避免污染 LLM 上下文
        return snippets
    if rc == 0 and stdout.strip():
        filtered, level = _filter_log_lines(stdout, keywords)
        if filtered:
            snippets.append(LogSnippet(
                source="kubectl:logs",
                level=level,
                timestamp="",
                content=filtered,
            ))
    return snippets


def _collect_logs_docker(
    log_paths: dict[str, str],
    keywords: "set[str]",
) -> list[LogSnippet]:
    """通过 docker logs 采集（Docker）"""
    snippets: list[LogSnippet] = []
    # docker daemon 日志文件
    file_snippets = _collect_logs_from_files(
        {k: v for k, v in log_paths.items() if k == "docker"},
        keywords,
    )
    snippets.extend(file_snippets)
    # 尝试列出容器并取日志（取前几个）
    # docker CLI 不可用时（典型：容器内部未挂载 docker.sock），
    # 回退到读容器内可见的日志文件，避免抛出"docker 未安装"硬错误
    # 污染 LLM 上下文导致幻觉
    try:
        rc, stdout, _ = _run_cmd(["docker", "ps", "--format", "{{.ID}}\\t{{.Names}}"])
    except CollectorToolNotFoundError:
        # 回退：读容器内可见的日志文件（同 UNKNOWN 运行时的做法）
        snippets.extend(_collect_logs_from_files(log_paths, keywords))
        return snippets
    if rc != 0:
        return snippets
    containers = []
    for line in stdout.splitlines()[:5]:  # 仅前 5 个容器，避免过载
        parts = line.split("\t")
        if len(parts) >= 2:
            containers.append((parts[0], parts[1]))
    for cid, cname in containers:
        try:
            rc2, stdout2, _ = _run_cmd(["docker", "logs", "--tail", "50", cid])
        except CollectorToolNotFoundError:
            break
        if rc2 != 0:
            continue
        filtered, level = _filter_log_lines(stdout2, keywords)
        if filtered:
            snippets.append(LogSnippet(
                source=f"docker:logs:{cname}",
                level=level,
                timestamp="",
                content=filtered,
            ))
    return snippets


# ===== Tool 3: 系统资源 =====


def collect_system_resources() -> dict:
    """采集系统资源使用情况（CPU/MEM/磁盘/负载）

    全环境通用，使用 /proc 与标准命令。

    Returns:
        {cpu_load, mem_used_gb, mem_total_gb, disk_usage, load_avg}
    """
    resources: dict = {}

    # 负载（/proc/loadavg）
    loadavg = _read_file("/proc/loadavg")
    if loadavg:
        parts = loadavg.split()
        if len(parts) >= 3:
            resources["load_avg"] = f"{parts[0]} {parts[1]} {parts[2]}"

    # 内存（/proc/meminfo）
    meminfo = _read_file("/proc/meminfo")
    if meminfo:
        mem_total_kb = _parse_meminfo_field(meminfo, "MemTotal:")
        mem_avail_kb = _parse_meminfo_field(meminfo, "MemAvailable:")
        if mem_total_kb:
            resources["mem_total_gb"] = round(mem_total_kb / 1024 / 1024, 1)
        if mem_total_kb and mem_avail_kb is not None:
            mem_used_kb = mem_total_kb - mem_avail_kb
            resources["mem_used_gb"] = round(mem_used_kb / 1024 / 1024, 1)

    # 磁盘使用（df）
    try:
        rc, stdout, _ = _run_cmd(["df", "-h", "--output=target,size,used,avail,pcent", "-x", "tmpfs", "-x", "devtmpfs"])
        if rc == 0:
            resources["disk_usage"] = stdout.strip()
    except CollectorToolNotFoundError:
        resources["disk_usage"] = "df 不可用"

    # inode 使用（df -i）
    # inode 耗尽时磁盘空间可能仍充足，仅看 df -h 无法发现；df -i 显示 IUse% 接近 100%
    # 即为 inode 耗尽故障（典型现象：No space left on device 但 df -h 有剩余空间）。
    try:
        rc, stdout, _ = _run_cmd(["df", "-i", "--output=target,inodes,iused,ifree,ipcent", "-x", "tmpfs", "-x", "devtmpfs"])
        if rc == 0:
            resources["inode_usage"] = stdout.strip()
    except CollectorToolNotFoundError:
        pass  # inode 采集非必需，缺失不阻断

    # CPU 使用（top 单次采样）
    try:
        rc, stdout, _ = _run_cmd(["top", "-bn1"])
        if rc == 0:
            # 提取 %Cpu 行
            for line in stdout.splitlines():
                if "%Cpu" in line or "Cpu(s)" in line:
                    resources["cpu_load"] = line.strip()
                    break
    except CollectorToolNotFoundError:
        pass  # top 非必需

    return resources


# ===== Tool 4: 网络连通性 =====


def collect_network_connectivity(
    env_type: EnvironmentType,
    container_runtime: ContainerRuntime | None,
    targets: "Sequence[str]",
) -> list[dict]:
    """采集网络连通性（ping/端口/CNI/iptables）

    Args:
        env_type: 环境类型
        container_runtime: 容器运行时子类型
        targets: 待检测目标（IP/主机名），为空时仅采集路由/CNI/iptables

    Returns:
        连通性结果列表 [{target, reachable, detail}]
    """
    results: list[dict] = []

    # ping 连通性
    for target in targets:
        reachable, detail = _ping_target(target)
        results.append({"target": target, "reachable": reachable, "detail": detail})

    # 路由/iptables（全环境通用）
    # 注意：iptables/CNI 采集成功不代表网络可达，用 collected=True 标记
    # 仅 ping 结果用 reachable 字段，供反幻觉校验区分
    iptables_result = _collect_iptables()
    if iptables_result:
        results.append({"target": "iptables", "collected": True, "detail": iptables_result})

    # 容器环境额外采集
    if env_type == EnvironmentType.CONTAINER:
        if container_runtime == ContainerRuntime.KUBERNETES:
            cni = _collect_cni_config()
            if cni:
                results.append({"target": "CNI", "collected": True, "detail": cni})
        elif container_runtime == ContainerRuntime.DOCKER:
            net = _collect_docker_network()
            if net:
                results.append({"target": "docker-network", "collected": True, "detail": net})
        else:
            # UNKNOWN 双路
            cni = _collect_cni_config()
            if cni:
                results.append({"target": "CNI", "collected": True, "detail": cni})
            net = _collect_docker_network()
            if net:
                results.append({"target": "docker-network", "collected": True, "detail": net})

    return results


def _ping_target(target: str) -> tuple[bool, str]:
    """ping 单个目标"""
    if shutil.which("ping") is None:
        return False, "ping 不可用"
    try:
        rc, _, stderr = _run_cmd(["ping", "-c", "2", "-W", "2", target])
        return rc == 0, "reachable" if rc == 0 else f"unreachable: {stderr.strip()[:100]}"
    except CollectorToolNotFoundError:
        return False, "ping 不可用"
    except CollectorPermissionError:
        return False, "ping 权限不足"


def _collect_iptables() -> str:
    """采集 iptables 规则"""
    try:
        rc, stdout, _ = _run_cmd(["iptables", "-S"])
        if rc == 0:
            return stdout.strip()
    except CollectorToolNotFoundError:
        pass
    except CollectorPermissionError:
        return "iptables: 权限不足（需 root）"
    return ""


def _collect_cni_config() -> str:
    """采集 CNI 配置（K8s）"""
    cni_dir = "/etc/cni/net.d"
    if not os.path.isdir(cni_dir):
        return ""
    parts: list[str] = []
    try:
        for entry in sorted(os.listdir(cni_dir)):
            content = _read_file(os.path.join(cni_dir, entry))
            if content:
                parts.append(f"--- {entry} ---\n{content}")
    except OSError:
        return ""
    return "\n".join(parts)


def _collect_docker_network() -> str:
    """采集 docker 网络信息"""
    try:
        rc, stdout, _ = _run_cmd(["docker", "network", "ls"])
        if rc == 0:
            return stdout.strip()
    except CollectorToolNotFoundError:
        pass
    return ""


# ===== 辅助函数 =====


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _filter_log_lines(content: str, keywords: "set[str]") -> tuple[str, str]:
    """按关键词过滤日志行，返回 (过滤后内容, 最高级别)

    保留含任一关键词的行；级别取所有匹配行中的最高级。
    """
    matched_lines: list[str] = []
    highest = "Info"
    for line in content.splitlines():
        clean = _ANSI_RE.sub("", line)
        lower = clean.lower()
        if not any(kw in lower for kw in keywords):
            continue
        matched_lines.append(clean)
        # 级别判定
        if "error" in lower or "fatal" in lower or "panic" in lower:
            highest = "ERROR"
        elif highest != "ERROR" and ("warning" in lower or "warn" in lower):
            highest = "Warning"
    return "\n".join(matched_lines), highest


def _parse_meminfo_field(meminfo: str, field: str) -> int | None:
    """从 /proc/meminfo 解析指定字段（KB）"""
    for line in meminfo.splitlines():
        if line.startswith(field):
            parts = line.split()
            if len(parts) >= 2:
                try:
                    return int(parts[1])
                except ValueError:
                    return None
    return None
