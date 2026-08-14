"""人工审核强制拦截（REQ-E-01 + REQ-F-03）

审核确认逻辑：根据 GuardResult 决定确认流程的严格程度。
确认输入走 stdin，不经 LLM 通道（双通道隔离）。

对齐 Safety_design.md §人工审核强制拦截设计。
"""

from __future__ import annotations

from galaxy_diag.shared.types import GuardResult, ReviewDecision


def review_confirm(guard_result: GuardResult) -> ReviewDecision:
    """审核确认判定 (E-01/F-03)

    根据 guard_result.level 决定确认流程：
    - pass: 普通确认 [y/N]，返回 YES 或 NO
    - warning/critical: 要求输入 CONFIRM，返回 YES/NO/EDIT

    注意：此函数**不直接收集 stdin 输入**，stdin 交互由
    workflow/cli/review_ui.py 负责。本函数只提供判定逻辑，
    由 engine.py 调用 review_ui 收集输入后调用本函数判定。

    实际使用中 engine.py 直接在 _do_reviewing 中实现交互逻辑，
    本函数作为逻辑入口供外部调用或测试。

    Args:
        guard_result: E-02 熔断结果

    Returns:
        ReviewDecision: YES/NO/EDIT

    不经 LLM，纯逻辑判定。
    """
    # 本函数的设计意图：提供审核判定的纯逻辑接口。
    # 在 engine.py 的 _do_reviewing 中，交互逻辑已内联实现
    # （操作菜单 y/n/e/d/r + CONFIRM 确认）。
    # 本函数供非交互场景（如 API 调用、自动化测试）使用。
    # 默认返回 NO（安全优先：无交互输入时偏向拒绝）
    return ReviewDecision.NO


def needs_confirm(guard_result: GuardResult) -> bool:
    """判断是否需要 CONFIRM 全称确认

    Args:
        guard_result: E-02 熔断结果

    Returns:
        True 表示需要输入 CONFIRM 确认
    """
    return guard_result.level in ("warning", "critical")
