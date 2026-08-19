"""反幻觉事实校验（采集后、诊断前）

用采集到的客观数据校验用户主观描述：
- 矛盾 → 终止工作流，告知"您的部署环境没有这些问题"
- 不矛盾 → 正常继续

纯规则映射，零 LLM 依赖，零幻觉风险。
"""

from __future__ import annotations

from dataclasses import dataclass

from galaxy_diag.shared.types import DiagnosticContext


@dataclass
class HallucinationCheckResult:
    """事实校验结果"""

    rule_id: str              # 命中的校验规则 ID
    contradiction: bool       # True=用户描述与采集数据矛盾
    message: str              # 矛盾时的输出消息；不矛盾时为空


# ===== 事实校验规则 =====

def _check_network_ok(problem_desc: str, ctx: DiagnosticContext) -> bool | None:
    """网络问题校验：所有目标可达 → 矛盾

    Returns: True=矛盾, False=不矛盾, None=无数据无法判断
    """
    keywords = ("网络", "不通", "ping", "network", "连通")
    if not any(kw in problem_desc.lower() for kw in keywords):
        return None  # 不涉及网络问题

    checks = ctx.network_checks
    if not checks:
        return None  # 无网络采集数据，无法判断

    # 所有目标可达 → 矛盾
    all_reachable = all(c.get("reachable") for c in checks)
    return all_reachable


def _check_service_ok(problem_desc: str, ctx: DiagnosticContext) -> bool | None:
    """服务问题校验：所有组件非 failed → 矛盾"""
    keywords = ("服务", "启动失败", "service", "fail", "启动")
    if not any(kw in problem_desc.lower() for kw in keywords):
        return None

    comps = ctx.component_status
    if not comps:
        return None

    all_ok = all(c.get("status") != "failed" for c in comps)
    return all_ok


def _check_mount_ok(problem_desc: str, ctx: DiagnosticContext) -> bool | None:
    """挂载问题校验：日志无 mount error / stale file handle → 矛盾"""
    keywords = ("挂载", "mount", "挂载失败")
    if not any(kw in problem_desc.lower() for kw in keywords):
        return None

    snippets = ctx.log_snippets
    if not snippets:
        return None

    all_text = " ".join(s.content.lower() for s in snippets)
    has_error = "mount error" in all_text or "stale file handle" in all_text
    return not has_error  # 无错误 → 矛盾


def _check_resource_ok(problem_desc: str, ctx: DiagnosticContext) -> bool | None:
    """内存/OOM 问题校验：无 OOM 且内存使用率<90% → 矛盾"""
    keywords = ("oom", "内存不足", "内存溢出", "out of memory")
    if not any(kw in problem_desc.lower() for kw in keywords):
        return None

    resources = ctx.system_resources
    if not resources:
        return None

    try:
        oom_count = int(resources.get("oom_count", "0"))
        mem_pct = float(resources.get("mem_used_percent", "0"))
    except (ValueError, TypeError):
        return None  # 非数值字符串无法判断

    # 无 OOM 且内存使用率 < 90% → 矛盾
    no_oom = oom_count == 0
    mem_ok = mem_pct < 90.0
    return no_oom and mem_ok


# 校验规则表：(rule_id, check_fn, contradiction_message)
_FACT_CHECK_RULES: list[tuple[str, object, str]] = [
    (
        "network_ok",
        _check_network_ok,
        "您的部署环境中网络连通性正常，不存在您描述的'网络不通'问题",
    ),
    (
        "service_ok",
        _check_service_ok,
        "您的部署环境中服务运行正常，不存在启动失败问题",
    ),
    (
        "mount_ok",
        _check_mount_ok,
        "您的部署环境中存储挂载状态正常，不存在挂载失败问题",
    ),
    (
        "resource_ok",
        _check_resource_ok,
        "您的部署环境中内存资源充足，不存在 OOM 问题",
    ),
]


def check_facts(
    problem_description: str,
    ctx: DiagnosticContext,
) -> HallucinationCheckResult | None:
    """事实校验：对比用户描述与采集数据，检测矛盾

    优先发现矛盾：扫描全部规则，任意规则返回 True（矛盾）即立即返回（短路）。
    无任何矛盾时，返回首个非 None 的 False 结果（供日志记录）。
    全部规则返回 None → 返回 None。

    Args:
        problem_description: 用户问题描述
        ctx: 采集产出的诊断上下文

    Returns:
        矛盾时返回 HallucinationCheckResult，不矛盾或无匹配返回 None
    """
    first_non_contradiction: HallucinationCheckResult | None = None
    for rule_id, check_fn, message in _FACT_CHECK_RULES:
        result = check_fn(problem_description, ctx)
        if result is None:
            continue  # 无数据/不涉及，跳过
        if result:
            # 矛盾 — 立即返回（短路优先返回矛盾）
            return HallucinationCheckResult(
                rule_id=rule_id,
                contradiction=True,
                message=message,
            )
        # 不矛盾 — 记住首个非矛盾结果（供日志返回），继续扫描后续规则
        if first_non_contradiction is None:
            first_non_contradiction = HallucinationCheckResult(
                rule_id=rule_id,
                contradiction=False,
                message="",
            )
    return first_non_contradiction
