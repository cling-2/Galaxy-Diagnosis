"""审核确认交互流程（F-03 预留）

当前仅定义函数签名，完整实现在 REQ-F-03 阶段。
关键约束：确认流程不经 LLM 通道（红线 2）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from galaxy_diag.shared.types import FixProposal


def review_confirm(operation: FixProposal) -> bool:
    """展示操作摘要，等待用户确认/拒绝/修改。

    F-03 阶段实现。
    当前返回 False（默认拒绝，安全优先）。
    """
    from galaxy_diag.workflow.cli.display import get_console, print_stub_notice
    print_stub_notice("REQ-F-03", "审核确认交互")
    return False


def review_reject(operation: FixProposal) -> None:
    """记录用户拒绝，不执行且不反复要求确认。

    F-03 阶段实现。
    """
    raise NotImplementedError("审核确认交互流程将在 REQ-F-03 阶段实现")


def review_modify(operation: FixProposal) -> FixProposal:
    """用户修改参数后重新提交（重新走 D-03 检测）。

    F-03 阶段实现。
    """
    raise NotImplementedError("审核确认交互流程将在 REQ-F-03 阶段实现")
