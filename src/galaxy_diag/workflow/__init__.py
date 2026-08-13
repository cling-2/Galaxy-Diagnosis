"""工作流编排模块

端到端状态机编排、CLI/TUI、审核确认交互、状态持久化。
对应需求 F-01/F-02/F-03。

用户可见步骤（7 步）：
  环境识别 → 信息收集 → 根因分析 → 修复建议 → 人工审核 → 执行 → 结果验证
"""

from galaxy_diag.workflow.engine import WorkflowEngine
from galaxy_diag.workflow.states import (
    STEP_LABELS,
    STEP_TO_USER_STEP,
    TOTAL_USER_STEPS,
    TRANSITIONS,
    is_valid_transition,
)

__all__ = [
    "WorkflowEngine",
    "STEP_LABELS",
    "STEP_TO_USER_STEP",
    "TOTAL_USER_STEPS",
    "TRANSITIONS",
    "is_valid_transition",
]
