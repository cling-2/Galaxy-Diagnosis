"""危险操作多维防护（REQ-E-02 逻辑层）

执行前熔断检查 (E-02)，三个检测维度：
1. 危险命令正则匹配 — 调用 patterns.DANGER_PATTERNS 匹配
2. 变量展开检测 — 防 CMD="rm -rf"; $CMD 绕过
3. 影响范围评估 — 评估涉及的服务/路径/挂载点

不经 LLM，纯硬编码正则 + 算法。
对齐 Safety_design.md §危险操作多维防护设计。

熔断分级策略：
- 无命中 → pass（普通 [y/N] 确认）
- 仅 WARNING → warning（要求 CONFIRM）
- 命中 CRITICAL → critical（要求 CONFIRM，输入不匹配则终止）
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from galaxy_diag.safety.patterns import DANGER_PATTERNS
from galaxy_diag.shared.constants import GALAXY_COMPONENTS
from galaxy_diag.shared.types import (
    CheckSeverity,
    DangerPattern,
    GuardResult,
)

if TYPE_CHECKING:
    from galaxy_diag.shared.types import EnvironmentType, FixProposal


# 变量赋值语句正则：VAR=value（捕获变量名和值）
_VAR_ASSIGN_PATTERN = re.compile(r"\b([A-Z_][A-Z0-9_]*)=(\"[^\"]*\"|'[^']*'|\S+)")

# 变量引用正则：$VAR 或 ${VAR}
_VAR_REF_PATTERN = re.compile(r"\$\{?([A-Z_][A-Z0-9_]*)\}?")

# systemctl/docker/kubectl 后跟的服务名
_SERVICE_PATTERN = re.compile(
    r"(?:systemctl\s+(?:start|stop|restart|enable|disable)|docker\s+(?:stop|start|restart)|"
    r"kubectl\s+(?:delete|scale|rollout))\s+([\w\-\.]+)"
)

# 文件路径模式（/etc/ /var/ /opt/ 等绝对路径）
_PATH_PATTERN = re.compile(r"(/(?:etc|var|opt|usr|root|home)/[^\s;|&<>]+)")


def _match_patterns(text: str) -> list[DangerPattern]:
    """对单段文本执行危险模式正则匹配，返回命中的模式列表"""
    matched: list[DangerPattern] = []
    for pat in DANGER_PATTERNS:
        try:
            if re.search(pat.pattern, text):
                matched.append(pat)
        except re.error:
            # 模式库正则异常时跳过该模式（fail-safe 保守策略：不因模式异常崩溃）
            continue
    return matched


def _detect_variable_expansion(script: str) -> list[DangerPattern]:
    """变量展开检测：防 CMD="rm -rf"; $CMD 绕过正则匹配

    思路：
    1. 扫描脚本中的变量赋值（VAR=value）
    2. 用危险模式正则匹配变量值本身
    3. 若变量值含危险片段，标记为危险变量
    4. 扫描对该变量的引用，展开后再匹配

    Returns:
        因变量展开而命中的危险模式列表
    """
    if not script:
        return []

    matched: list[DangerPattern] = []

    # 步骤 1-2：收集危险变量（变量值本身含危险片段）
    danger_vars: dict[str, list[DangerPattern]] = {}
    for var_name, var_value in _VAR_ASSIGN_PATTERN.findall(script):
        var_value_clean = var_value.strip("\"'")
        var_matched = _match_patterns(var_value_clean)
        if var_matched:
            danger_vars[var_name] = var_matched

    # 步骤 3-4：扫描变量引用，对引用展开后匹配
    if danger_vars:
        matched.extend(_match_patterns(script))  # 脚本整体再匹配一次
        for var_name, var_matched in danger_vars.items():
            # 标记被危险变量引用的命令
            refs = _VAR_REF_PATTERN.findall(script)
            if var_name in refs:
                matched.extend(var_matched)

    return matched


def _assess_impact(text: str) -> tuple[list[str], list[str], list[str]]:
    """评估影响范围（danger.py 函数内局部逻辑，不暴露跨域类型）

    提取涉及的文件路径、服务名、银河平台组件，用于生成 summary 字符串。

    Returns:
        (affected_paths, affected_services, affected_galaxy_components)
    """
    affected_paths = list(set(_PATH_PATTERN.findall(text)))

    raw_services = _SERVICE_PATTERN.findall(text)
    affected_services = list(set(raw_services))

    # 与 GALAXY_COMPONENTS 交叉，标注受影响的银河平台组件
    affected_galaxy: list[str] = []
    for comp in GALAXY_COMPONENTS:
        if comp in text:
            affected_galaxy.append(comp)

    return affected_paths, affected_services, affected_galaxy


def _build_impact_summary(
    paths: list[str], services: list[str], galaxy: list[str]
) -> str:
    """构建影响范围一句话汇总字符串"""
    parts: list[str] = []
    if galaxy:
        parts.append(f"影响银河组件: {'、'.join(galaxy)}")
    if services:
        parts.append(f"影响服务: {'、'.join(services)}")
    if paths:
        # 路径可能很多，只显示前 3 个 + 计数
        shown = paths[:3]
        extra = f" 等 {len(paths)} 个" if len(paths) > 3 else ""
        parts.append(f"影响文件: {'、'.join(shown)}{extra}")
    return "；".join(parts) if parts else "无明确影响范围"


def execution_guard_check(proposal: "FixProposal", env_type: "EnvironmentType") -> GuardResult:
    """执行前熔断检查 (E-02)

    Args:
        proposal: 待执行的修复建议
        env_type: 当前环境类型（影响范围评估参考）

    Returns:
        GuardResult: 熔断结果，含 level / matched_patterns / impact_summary

    不经 LLM，纯硬编码正则 + 算法。
    """
    # 收集所有待检测文本：逐条命令 + 脚本（如有）
    texts: list[str] = []
    for cmd in proposal.commands:
        texts.append(cmd.command)
    if proposal.script:
        texts.append(proposal.script)

    full_text = "\n".join(texts)

    # ① 危险命令正则匹配
    matched: list[DangerPattern] = []
    for text in texts:
        matched.extend(_match_patterns(text))

    # ② 变量展开检测（仅对脚本）
    if proposal.script:
        var_matched = _detect_variable_expansion(proposal.script)
        # 去重
        existing_descs = {m.description for m in matched}
        for m in var_matched:
            if m.description not in existing_descs:
                matched.append(m)
                existing_descs.add(m.description)

    # ③ 影响范围评估
    paths, services, galaxy = _assess_impact(full_text)
    impact_summary = _build_impact_summary(paths, services, galaxy)

    # 同步填充 FixProposal.impact_scope（已有 str 字段）
    proposal.impact_scope = impact_summary

    # 分级：取最高 severity
    if not matched:
        level = "pass"
        message = "执行前熔断通过，无危险模式命中"
    elif any(m.severity == CheckSeverity.CRITICAL for m in matched):
        level = "critical"
        descs = "、".join(m.description for m in matched if m.severity == CheckSeverity.CRITICAL)
        message = f"执行前熔断检测到危险操作: {descs}"
    else:
        level = "warning"
        descs = "、".join(m.description for m in matched)
        message = f"执行前熔断检测到警告操作: {descs}"

    return GuardResult(
        level=level,  # type: ignore[arg-type]
        matched_patterns=matched,
        impact_summary=impact_summary,
        message=message,
    )
