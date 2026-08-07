"""工作流状态机：状态定义与转换规则

对应 workflow-design.md §2 状态机设计。
定义合法的状态转换关系，供 engine.py 校验。
"""

from __future__ import annotations

from galaxy_diag.shared.types import WorkflowStep


# 步骤中文标签（供 UI 展示）
STEP_LABELS: dict[WorkflowStep, str] = {
    WorkflowStep.ENV_RECOGNISING: "环境感知",
    WorkflowStep.COLLECTING: "信息采集",
    WorkflowStep.DIAGNOSING: "根因分析",
    WorkflowStep.PLANNING: "修复建议生成",
    WorkflowStep.SECURITY_CHECKING: "安全检测",
    WorkflowStep.REVIEWING: "人工审核",
    WorkflowStep.SNAPSHOT: "创建快照",
    WorkflowStep.EXECUTING: "执行修复",
    WorkflowStep.VERIFYING: "结果验证",
}

# 步骤描述（供 UI 展示）
STEP_DESCRIPTIONS: dict[WorkflowStep, str] = {
    WorkflowStep.ENV_RECOGNISING: "识别运行环境类型 (VM/容器/裸机) 并采集软硬件信息",
    WorkflowStep.COLLECTING: "采集日志、系统状态等诊断信息",
    WorkflowStep.DIAGNOSING: "基于诊断上下文推理根因，声明不确定性",
    WorkflowStep.PLANNING: "生成修复命令/脚本建议",
    WorkflowStep.SECURITY_CHECKING: "对修复建议做语法/危险/兼容性多维检测",
    WorkflowStep.REVIEWING: "人工审核修复建议 (确认/拒绝/修改)",
    WorkflowStep.SNAPSHOT: "执行前创建恢复快照",
    WorkflowStep.EXECUTING: "按步骤执行修复并监控",
    WorkflowStep.VERIFYING: "验证修复是否生效",
}

# 有序步骤序列（快乐路径）
HAPPY_PATH: list[WorkflowStep] = [
    WorkflowStep.ENV_RECOGNISING,
    WorkflowStep.COLLECTING,
    WorkflowStep.DIAGNOSING,
    WorkflowStep.PLANNING,
    WorkflowStep.SECURITY_CHECKING,
    WorkflowStep.REVIEWING,
    WorkflowStep.SNAPSHOT,
    WorkflowStep.EXECUTING,
    WorkflowStep.VERIFYING,
]

# 合法的状态转换表（对应 workflow-design.md §2.3 转换规则）
# key: 当前状态 → value: 允许的下一状态集合（不含终态，终态由特殊标记处理）
TRANSITIONS: dict[WorkflowStep, list[WorkflowStep]] = {
    WorkflowStep.ENV_RECOGNISING: [WorkflowStep.COLLECTING],
    WorkflowStep.COLLECTING: [WorkflowStep.DIAGNOSING],
    WorkflowStep.DIAGNOSING: [
        WorkflowStep.PLANNING,           # confidence = CONFIRMED / SUSPECTED
        WorkflowStep.COLLECTING,         # confidence = INSUFFICIENT，回退补充采集
    ],
    WorkflowStep.PLANNING: [WorkflowStep.SECURITY_CHECKING],
    WorkflowStep.SECURITY_CHECKING: [
        WorkflowStep.REVIEWING,          # 检测通过
        WorkflowStep.PLANNING,           # 检测失败，重新生成
    ],
    WorkflowStep.REVIEWING: [
        WorkflowStep.SNAPSHOT,           # 用户确认 yes
        WorkflowStep.PLANNING,           # 用户编辑 edit
    ],
    WorkflowStep.SNAPSHOT: [WorkflowStep.EXECUTING],
    WorkflowStep.EXECUTING: [
        WorkflowStep.VERIFYING,          # 执行成功
    ],
    WorkflowStep.VERIFYING: [],  # 完成（DONE）或回 DIAGNOSING（验证失败）由 engine 处理
}

# VERIFYING 的特殊转换：成功→DONE，失败→回 DIAGNOSING
# 因 DONE 不是 WorkflowStep 枚举值，单独定义
VERIFYING_NEXT_ON_SUCCESS = "done"      # 标记为完成
VERIFYING_NEXT_ON_FAILURE = WorkflowStep.DIAGNOSING  # 验证失败重新诊断

# REVIEWING 终止（用户拒绝 no）的特殊标记
REVIEWING_NEXT_ON_REJECT = "rejected"   # 标记为拒绝

# EXECUTING 失败的特殊标记：回滚后终止
EXECUTING_NEXT_ON_FAILURE = "rollback"  # 标记为回滚

# 可跳过的步骤（§2.4 短路场景）
# key: 步骤 → 该步骤允许的跳过目标
SKIP_TARGETS: dict[WorkflowStep, list[WorkflowStep]] = {
    # 已知故障模式：COLLECTING 后可跳过 DIAGNOSING 直接 PLANNING
    WorkflowStep.COLLECTING: [WorkflowStep.PLANNING],
}


def is_valid_transition(current: WorkflowStep, target: WorkflowStep) -> bool:
    """检查状态转换是否合法

    Args:
        current: 当前状态
        target: 目标状态

    Returns:
        True 表示转换合法
    """
    allowed = TRANSITIONS.get(current, [])
    return target in allowed


def validate_transition(current: WorkflowStep, target: WorkflowStep) -> None:
    """校验状态转换，非法则抛异常

    Args:
        current: 当前状态
        target: 目标状态

    Raises:
        ValueError: 非法状态转换
    """
    if not is_valid_transition(current, target):
        allowed = TRANSITIONS.get(current, [])
        raise ValueError(
            f"非法状态转换: {current.value} → {target.value}。"
            f"允许的下一状态: {[s.value for s in allowed]}"
        )
