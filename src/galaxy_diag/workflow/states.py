"""工作流状态机：状态定义与转换规则

对应 workflow-design.md §2 状态机设计。
定义合法的状态转换关系，供 engine.py 校验。

用户可见步骤（7 步）：
  环境识别 → 信息收集 → 根因分析 → 修复建议 → 人工审核 → 执行 → 结果验证

内部状态（10 个）与用户可见步骤的映射：
  - 修复建议 = PLANNING + SECURITY_CHECKING（生成后检测在建议末尾执行）
  - 人工审核 = EXECUTION_GUARD（执行前熔断）+ REVIEWING（审核确认）
  - 执行     = SNAPSHOT（自动创建快照）+ EXECUTING
"""

from __future__ import annotations

from galaxy_diag.shared.types import WorkflowStep


# ===== 用户可见步骤 =====

# 用户可见步骤总数
TOTAL_USER_STEPS = 7

# 内部步骤 → 用户可见步骤映射 (step_number, label)
# 同一用户步骤内的多个内部步骤映射到相同的用户步骤编号
STEP_TO_USER_STEP: dict[WorkflowStep, tuple[int, str]] = {
    WorkflowStep.ENV_RECOGNISING: (1, "环境识别"),
    WorkflowStep.COLLECTING: (2, "信息收集"),
    WorkflowStep.DIAGNOSING: (3, "根因分析"),
    WorkflowStep.PLANNING: (4, "修复建议"),
    WorkflowStep.SECURITY_CHECKING: (4, "修复建议"),   # 生成后检测在修复建议末尾执行
    WorkflowStep.EXECUTION_GUARD: (5, "人工审核"),      # 执行前熔断在人工审核前执行
    WorkflowStep.REVIEWING: (5, "人工审核"),             # 人工审核确认
    WorkflowStep.SNAPSHOT: (6, "执行"),                  # 快照自动创建，归入执行步骤
    WorkflowStep.EXECUTING: (6, "执行"),
    WorkflowStep.VERIFYING: (7, "结果验证"),
}


# ===== 步骤标签与描述 =====

# 步骤中文标签（供内部日志 / history 使用）
STEP_LABELS: dict[WorkflowStep, str] = {
    WorkflowStep.ENV_RECOGNISING: "环境识别",
    WorkflowStep.COLLECTING: "信息采集",
    WorkflowStep.DIAGNOSING: "根因分析",
    WorkflowStep.PLANNING: "修复建议",
    WorkflowStep.SECURITY_CHECKING: "生成后检测",
    WorkflowStep.EXECUTION_GUARD: "执行前熔断",
    WorkflowStep.REVIEWING: "人工审核",
    WorkflowStep.SNAPSHOT: "创建快照",
    WorkflowStep.EXECUTING: "执行修复",
    WorkflowStep.VERIFYING: "结果验证",
}

# 步骤描述（供 UI 展示 / verbose 模式）
STEP_DESCRIPTIONS: dict[WorkflowStep, str] = {
    WorkflowStep.ENV_RECOGNISING: "识别运行环境类型 (VM/容器/裸机) 并采集软硬件信息",
    WorkflowStep.COLLECTING: "采集日志、系统状态等诊断信息",
    WorkflowStep.DIAGNOSING: "基于诊断上下文推理根因，声明不确定性",
    WorkflowStep.PLANNING: "生成修复命令/脚本建议",
    WorkflowStep.SECURITY_CHECKING: "D-03 生成后检测：语法+兼容性+危险建议性警告（在修复建议步骤末尾执行）",
    WorkflowStep.EXECUTION_GUARD: "E-02 执行前熔断：危险命令强制拦截+影响范围评估（在人工审核前执行）",
    WorkflowStep.REVIEWING: "人工审核修复建议 (确认/拒绝/修改)",
    WorkflowStep.SNAPSHOT: "执行前自动创建恢复快照",
    WorkflowStep.EXECUTING: "按步骤执行修复并监控",
    WorkflowStep.VERIFYING: "验证修复是否生效",
}


# ===== 有序步骤序列 =====

# 快乐路径（内部状态机顺序）
# 注意：EXECUTION_GUARD 在 REVIEWING 前面（熔断先于审核）
#       SECURITY_CHECKING 在 PLANNING 后面（检测在建议生成后）
#       SNAPSHOT 在 REVIEWING 后面（审核通过后自动创建快照）
HAPPY_PATH: list[WorkflowStep] = [
    WorkflowStep.ENV_RECOGNISING,
    WorkflowStep.COLLECTING,
    WorkflowStep.DIAGNOSING,
    WorkflowStep.PLANNING,
    WorkflowStep.SECURITY_CHECKING,
    WorkflowStep.EXECUTION_GUARD,   # 熔断在审核前
    WorkflowStep.REVIEWING,
    WorkflowStep.SNAPSHOT,          # 审核后自动快照
    WorkflowStep.EXECUTING,
    WorkflowStep.VERIFYING,
]


# ===== 合法状态转换表 =====

# 对应 workflow-design.md §2.3 转换规则
# key: 当前状态 → value: 允许的下一状态集合（不含终态，终态由特殊标记处理）
TRANSITIONS: dict[WorkflowStep, list[WorkflowStep]] = {
    WorkflowStep.ENV_RECOGNISING: [
        WorkflowStep.COLLECTING,        # 正常流程
        WorkflowStep.PLANNING,          # B类：已知故障模式跳过 COLLECTING+DIAGNOSING
    ],
    WorkflowStep.COLLECTING: [
        WorkflowStep.DIAGNOSING,            # 正常流程
        WorkflowStep.PLANNING,              # 已知故障模式短路（REQ-F-02 验收标准 4）
    ],
    WorkflowStep.DIAGNOSING: [
        WorkflowStep.PLANNING,           # confidence = CONFIRMED / SUSPECTED
        WorkflowStep.COLLECTING,         # confidence = INSUFFICIENT，回退补充采集
    ],
    WorkflowStep.PLANNING: [WorkflowStep.SECURITY_CHECKING],
    WorkflowStep.SECURITY_CHECKING: [
        WorkflowStep.EXECUTION_GUARD,    # 检测通过 → 执行前熔断
        WorkflowStep.PLANNING,           # 检测失败 (CRITICAL)，重新生成
    ],
    WorkflowStep.EXECUTION_GUARD: [
        WorkflowStep.REVIEWING,          # 熔断通过 → 人工审核
        # WARNING / CRITICAL 时也进入 REVIEWING，但要求 CONFIRM 确认（由 engine 处理）
    ],
    WorkflowStep.REVIEWING: [
        WorkflowStep.SNAPSHOT,           # 用户确认 yes → 自动创建快照
        WorkflowStep.SECURITY_CHECKING,  # 用户编辑 edit → 重走 D-03 检测
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
    # B类跳过：ENV_RECOGNISING 后已知故障模式直接跳到 PLANNING
    WorkflowStep.ENV_RECOGNISING: [WorkflowStep.PLANNING],
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
