"""诊断上下文组装（REQ-C-01）

顶层编排 build_diagnostic_context()：关键词匹配 → 定向采集 → 预处理 → 组装 DiagnosticContext。

这是工作流引擎 _do_collecting 的唯一入口，也供 DIAGNOSING 回退增量采集复用。

对齐 Diagnostic_collection_design.md §顶层编排函数 §关键词→Tool 映射 §预处理与体积控制。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from galaxy_diag.diagnoser.tools import (
    collect_component_status,
    collect_network_connectivity,
    collect_service_logs,
    collect_system_resources,
)
from galaxy_diag.shared.constants import GALAXY_COMPONENTS, KEY_LOG_PATHS
from galaxy_diag.shared.errors import CollectorError
from galaxy_diag.shared.types import DiagnosticContext, LogSnippet

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from galaxy_diag.shared.types import EnvInfo


# ===== 关键词 → Tool 映射 =====

# Tool 名常量（与函数名一致，便于 collected_tools 追溯）
TOOL_COMPONENT = "collect_component_status"
TOOL_LOGS = "collect_service_logs"
TOOL_RESOURCES = "collect_system_resources"
TOOL_NETWORK = "collect_network_connectivity"

# 关键词分组：每组关键词命中后激活对应 Tool
# 多组可同时命中（一个故障可能既涉及网络又涉及日志）
_KEYWORD_GROUPS: list[tuple[str, tuple[str, ...]]] = [
    (TOOL_NETWORK, (
        "网络", "不通", "ping", "连通", "network", "cni", "iptables",
        "路由", "route", "dns",
    )),
    (TOOL_LOGS, (
        "磁盘", "挂载", "存储", "盘", "识别", "disk", "mount", "storage",
        "日志", "log", "报错", "错误", "error",
    )),
    (TOOL_COMPONENT, (
        "服务", "启动", "失败", "状态", "service", "fail", "status",
        "组件", "component", "进程", "process", "容器", "pod", "k8s", "container",
    )),
    (TOOL_RESOURCES, (
        "慢", "卡", "资源", "cpu", "内存", "负载", "slow", "resource",
        "memory", "load", "oom",
    )),
]

# 体积预算（字节）
_TOTAL_BUDGET_BYTES = 32 * 1024
_PER_SNIPPET_MAX_CHARS = 2048


def match_tools_by_keywords(problem_description: str) -> set[str]:
    """根据问题描述匹配待采集的 Tool 集合

    调度策略（对齐设计文档）：
    1. 命中关键词的 Tool 加入集合
    2. collect_system_resources 始终纳入（通用基础项）
    3. 未命中任何关键词时：collect_component_status + collect_system_resources（最小基础集）

    Args:
        problem_description: 用户问题描述

    Returns:
        待采集 Tool 名集合
    """
    if not problem_description:
        # 空描述：最小基础集
        return {TOOL_COMPONENT, TOOL_RESOURCES}

    lower = problem_description.lower()
    matched: set[str] = set()
    for tool_name, keywords in _KEYWORD_GROUPS:
        if any(kw in lower for kw in keywords):
            matched.add(tool_name)

    # 兜底：始终采集资源
    matched.add(TOOL_RESOURCES)

    # 未命中任何业务关键词 → 最小基础集
    if matched == {TOOL_RESOURCES}:
        matched.add(TOOL_COMPONENT)

    return matched


def extract_keywords(problem_description: str) -> list[str]:
    """从问题描述提取日志过滤关键词"""
    if not problem_description:
        return []
    # 简单分词：按非字母数字汉字字符切分，保留长度>=2 的词
    import re
    tokens = re.findall(r"[\w一-鿿]{2,}", problem_description)
    return tokens


# ===== C类：按需精简硬件采集 =====

# 需要硬件采集的关键词（命中任一即需要）
_HARDWARE_NEEDED_KEYWORDS: tuple[str, ...] = (
    "磁盘", "盘", "disk", "I/O", "io error", "smart", "raid",
    "固件", "firmware", "存储", "storage", "mount", "挂载",
    "利旧", "控制器", "数据盘", "lsblk", "fsck",
)

# 不需要硬件采集的关键词（仅当未命中 _HARDWARE_NEEDED_KEYWORDS 时生效）
_HARDWARE_NOT_NEEDED_KEYWORDS: tuple[str, ...] = (
    "网络", "network", "ping", "cni", "iptables",
    "路由", "dns", "服务", "启动", "service", "容器",
    "pod", "k8s", "oom", "内存不足", "内存溢出",
)


def should_collect_hardware(problem_description: str) -> bool:
    """根据问题描述判断是否需要采集完整硬件信息（C类精简采集）

    策略：
    1. 命中"需要硬件"关键词 → True
    2. 未命中"需要硬件"但命中"不需要硬件" → False
    3. 均未命中 → True（保守：不确定就采）

    Args:
        problem_description: 用户问题描述

    Returns:
        True 需要采集硬件，False 可跳过
    """
    if not problem_description:
        return True

    lower = problem_description.lower()

    # 优先级：需要硬件的关键词优先
    if any(kw.lower() in lower for kw in _HARDWARE_NEEDED_KEYWORDS):
        return True

    if any(kw.lower() in lower for kw in _HARDWARE_NOT_NEEDED_KEYWORDS):
        return False

    # 默认采集（保守）
    return True


# ===== 预处理与体积控制 =====


def preprocess_logs(
    snippets: list[LogSnippet],
    budget_kb: int = 32,
) -> list[LogSnippet]:
    """日志预处理：单条截断 + 总体积预算控制

    优先级：ERROR > Warning > Info（超预算时丢弃低优先级）

    Args:
        snippets: 原始日志片段
        budget_kb: 总预算（KB）

    Returns:
        预处理后的日志片段
    """
    if not snippets:
        return []

    # 1. 单条截断
    for snip in snippets:
        if len(snip.content) > _PER_SNIPPET_MAX_CHARS:
            snip.content = snip.content[:_PER_SNIPPET_MAX_CHARS] + "\n[truncated]"
            snip.truncated = True

    # 2. 按优先级排序（ERROR > Warning > Info）
    priority = {"ERROR": 0, "Warning": 1, "Info": 2}
    sorted_snippets = sorted(
        snippets,
        key=lambda s: priority.get(s.level, 3),
    )

    # 3. 总预算控制
    budget_bytes = budget_kb * 1024
    kept: list[LogSnippet] = []
    used = 0
    for snip in sorted_snippets:
        size = len(snip.content.encode("utf-8"))
        if used + size > budget_bytes and kept:
            # 预算超限，丢弃低优先级
            continue
        kept.append(snip)
        used += size

    return kept


# ===== 安全采集包装 =====


def _safe_collect(
    fn: "Callable",
    warnings: list[str],
    *args,
    **kwargs,
):
    """包裹 Tool 调用，捕获异常降级

    单项失败返回空值 + warning，不阻断其他 Tool。
    返回值类型与 fn 一致（失败时返回 fn 的"空"形态）。
    """
    fn_name = getattr(fn, "__name__", str(fn))
    try:
        return fn(*args, **kwargs)
    except CollectorError as e:
        warnings.append(f"{fn_name} 采集失败: {e.message}")
        if e.hint:
            warnings.append(f"  💡 {e.hint}")
        # 返回对应空形态
        return _empty_result_for_name(fn_name)


def _empty_result_for_name(name: str):
    """根据函数/工具名返回空形态"""
    if name in (TOOL_COMPONENT, TOOL_LOGS, TOOL_NETWORK):
        return []
    if name == TOOL_RESOURCES:
        return {}
    return []


# ===== 被动接收：用户日志 =====


def _load_user_logs(
    log_files: "Sequence[str] | None",
    warnings: list[str],
) -> list[str]:
    """读取用户上传的日志文件内容

    每个文件读取尾部 8KB 作为日志片段。文件读取失败记 warning，不阻断。
    """
    if not log_files:
        return []

    user_logs: list[str] = []
    for path in log_files:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                # 读尾部 8KB
                import os as _os
                size = _os.path.getsize(path)
                if size > 8192:
                    f.seek(size - 8192)
                content = f.read()
            user_logs.append(f"[user-upload:{path}]\n{content.strip()}")
        except OSError as e:
            warnings.append(f"用户日志文件读取失败: {path}（{e}）")
    return user_logs


# ===== raw_output 摘要组装 =====


def build_raw_summary(ctx_fields: dict) -> dict:
    """组装 raw_output 摘要（供 LLM 上下文）

    将结构化字段压缩为可注入 Prompt 的文本摘要，控制单条体积。
    用分隔标记包裹不可信数据（Prompt 注入防护）。
    """
    summary: dict[str, str] = {}

    # 问题描述（标注为用户输入）
    desc = ctx_fields.get("problem_description", "")
    if desc:
        summary["problem_description"] = f"<user-input>\n{desc}\n</user-input>"

    # 组件状态
    components = ctx_fields.get("component_status", [])
    if components:
        lines = [f"- {c['name']}: {c['status']} ({c.get('detail', '')})" for c in components]
        summary["component_status"] = "\n".join(lines)

    # 日志片段（用 <log> 标记包裹，防注入）
    snippets = ctx_fields.get("log_snippets", [])
    if snippets:
        parts = []
        for s in snippets:
            parts.append(
                f"<log source=\"{s.source}\" level=\"{s.level}\">\n{s.content}\n</log>"
            )
        summary["log_snippets"] = "\n\n".join(parts)

    # 系统资源
    resources = ctx_fields.get("system_resources", {})
    if resources:
        summary["system_resources"] = "\n".join(
            f"{k}: {v}" for k, v in resources.items()
        )

    # 网络连通性
    net = ctx_fields.get("network_checks", [])
    if net:
        lines = [f"- {n['target']}: reachable={n['reachable']} ({n.get('detail', '')[:100]})" for n in net]
        summary["network_checks"] = "\n".join(lines)

    # 用户上传日志
    user = ctx_fields.get("user_provided", [])
    if user:
        summary["user_provided"] = "\n\n".join(
            f"<user-log>\n{u}\n</user-log>" for u in user
        )

    # 总体积再截断一次
    return _truncate_summary(summary)


def _truncate_summary(summary: dict[str, str]) -> dict[str, str]:
    """截断 raw_summary 各项，避免单项过大"""
    max_chars = _PER_SNIPPET_MAX_CHARS
    truncated: dict[str, str] = {}
    for key, value in summary.items():
        if not isinstance(value, str):
            value = str(value)
        if len(value) > max_chars:
            value = value[:max_chars] + "\n[truncated]"
        truncated[key] = value
    return truncated


# ===== 顶层编排 =====


def build_diagnostic_context(
    problem_description: str,
    env_info: "EnvInfo",
    user_log_files: "Sequence[str] | None" = None,
    existing_context: "DiagnosticContext | None" = None,
) -> DiagnosticContext:
    """COLLECTING 顶层编排：关键词匹配 → 定向采集 → 预处理 → 组装上下文

    Args:
        problem_description: 用户问题描述（含补充）
        env_info: 环境感知产出（ENV_RECOGNISING 步骤）
        user_log_files: 用户上传的日志文件路径（被动接收）
        existing_context: 已有的诊断上下文（增量采集时跳过已调用的 Tool）

    Returns:
        DiagnosticContext 结构化诊断上下文

    Raises:
        CollectorError: 所有 Tool 均失败时抛出（由 engine 捕获）
    """
    env_type = env_info.env_type
    container_runtime = env_info.container_runtime

    # 1. 关键词匹配，决定采集哪些 Tool
    tools_to_run = match_tools_by_keywords(problem_description)

    # 1.5 增量采集：若已有上下文，仅调用新增 Tool
    if existing_context is not None:
        already_collected = set(existing_context.collected_tools)
        new_tools = tools_to_run - already_collected
    else:
        new_tools = tools_to_run

    # 2. 定向采集（各 Tool 独立 try/except，单项失败不阻断）
    warnings: list[str] = []
    component_status: list[dict] = []
    log_snippets: list[LogSnippet] = []
    system_resources: dict = {}
    network_checks: list[dict] = []

    if TOOL_COMPONENT in new_tools:
        component_status = _safe_collect(
            collect_component_status,
            warnings,
            env_type,
            container_runtime,
            list(GALAXY_COMPONENTS),
        )
    if TOOL_LOGS in new_tools:
        log_snippets = _safe_collect(
            collect_service_logs,
            warnings,
            env_type,
            container_runtime,
            KEY_LOG_PATHS,
            extract_keywords(problem_description),
        )
    # collect_system_resources 始终采集（兜底）
    system_resources = _safe_collect(
        collect_system_resources, warnings
    )
    if TOOL_NETWORK in new_tools:
        network_checks = _safe_collect(
            collect_network_connectivity,
            warnings,
            env_type,
            container_runtime,
            [],  # targets 留空，仅采集路由/CNI/iptables
        )

    # 3. 被动接收：用户上传日志
    user_provided = _load_user_logs(user_log_files, warnings)

    # 3.5 增量合并：若已有上下文，将新采集结果追加到已有数据
    if existing_context is not None:
        component_status = existing_context.component_status + component_status
        log_snippets = existing_context.log_snippets + log_snippets
        system_resources = {**existing_context.system_resources, **system_resources}
        network_checks = existing_context.network_checks + network_checks
        user_provided = existing_context.user_provided + user_provided
        warnings = existing_context.collection_warnings + warnings

    # 4. 预处理与体积控制
    log_snippets = preprocess_logs(log_snippets, budget_kb=32)

    # 5. 整体失败判定：所有主动采集均空且无用户日志
    active_empty = (
        not component_status
        and not log_snippets
        and not system_resources
        and not network_checks
    )
    if active_empty and not user_provided:
        # 所有 Tool 均失败 → 抛出
        raise CollectorError(
            "所有诊断采集 Tool 均失败，无法构建诊断上下文",
            hint="请检查采集工具（systemctl/kubectl/docker/journalctl）是否可用，"
                 "或通过 --log-file 上传日志",
        )

    # 6. 组装 collected_tools（实际成功调用的，可追溯）
    if existing_context is not None:
        collected_tools = list(set(existing_context.collected_tools) | tools_to_run)
    else:
        collected_tools = list(tools_to_run)

    # 7. 组装 DiagnosticContext
    ctx = DiagnosticContext(
        problem_description=problem_description,
        env_info_ref=env_type,
        container_runtime=container_runtime,
        component_status=component_status,
        log_snippets=log_snippets,
        system_resources=system_resources,
        network_checks=network_checks,
        user_provided=user_provided,
        collection_warnings=warnings,
        raw_output={},  # 先占位，下面填
        collected_tools=collected_tools,
    )

    # 8. 组装 raw_output 摘要
    ctx.raw_output = build_raw_summary({
        "problem_description": ctx.problem_description,
        "component_status": ctx.component_status,
        "log_snippets": ctx.log_snippets,
        "system_resources": ctx.system_resources,
        "network_checks": ctx.network_checks,
        "user_provided": ctx.user_provided,
    })

    return ctx
