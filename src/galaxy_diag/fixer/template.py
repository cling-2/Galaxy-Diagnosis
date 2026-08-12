"""占位符模板引擎（REQ-D-01）

将 LLM 输出的含占位符命令转化为用户可交互编辑的 CommandTemplate 列表。
确定性纯函数——不依赖 LLM、不修改状态、无副作用。

对应设计文档 §占位符模板引擎。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from galaxy_diag.shared.types import (
    CommandTemplate,
    FixStep,
)

# 占位符模式：<UPPER_CASE>，如 <IP>、<MOUNT_POINT>、<DRIVER_MODULE>
PLACEHOLDER_PATTERN = re.compile(r'<([A-Z_][A-Z0-9_]*)>')


# ===== 编辑审计记录 =====


@dataclass
class EditRecord:
    """编辑操作记录（供审计日志消费）

    template.py 本身不写日志（纯函数原则），返回此记录供 engine.py 写入 safety/audit.py。
    """

    action: Literal["apply_param", "remove_step", "reorder_steps"]
    detail: str          # 如 "将 <IP> 替换为 10.0.1.100" 或 "删除步骤 3: lsblk"
    timestamp: datetime  # 由调用方填入


# ===== FixStep → CommandTemplate 转换 =====


def render_command_template(step: FixStep) -> CommandTemplate:
    """将 FixStep 渲染为 CommandTemplate

    核心逻辑：识别占位符 → 与 parameters 合并 → 构建可编辑结构。
    若 LLM 漏报占位符（command 中有 <X> 但 parameters 中无），做兜底补全。
    """
    # 1. 从 command 中识别所有占位符
    placeholders_in_cmd = set(PLACEHOLDER_PATTERN.findall(step.command))

    # 2. 与 step.parameters 合并（以 parameters 中的默认值为准）
    editable_params: dict[str, str] = {}
    for ph in placeholders_in_cmd:
        if ph in step.parameters:
            editable_params[ph] = step.parameters[ph]
        else:
            editable_params[ph] = f"<{ph}>"  # 无默认值，保留占位符标记

    return CommandTemplate(
        command=step.command,
        description=step.description,
        risk_note=step.risk_note,
        editable_params=editable_params,
        is_verification=step.is_verification,
    )


def render_all(steps: list[FixStep]) -> list[CommandTemplate]:
    """批量渲染 FixStep 列表为 CommandTemplate 列表"""
    return [render_command_template(s) for s in steps]


# ===== 参数编辑 =====


def apply_param_values(
    template: CommandTemplate,
    values: dict[str, str],
) -> CommandTemplate:
    """将用户填入的参数值应用到命令模板

    返回新的 CommandTemplate（不修改原对象）。
    未提供的占位符保留原样。values 中不在 editable_params 中的键被忽略。
    """
    new_command = template.command
    new_params = dict(template.editable_params)

    for name, value in values.items():
        if name in new_params:
            new_command = new_command.replace(f"<{name}>", value)
            new_params[name] = value

    return CommandTemplate(
        command=new_command,
        description=template.description,
        risk_note=template.risk_note,
        editable_params=new_params,
        is_verification=template.is_verification,
    )


def is_fully_resolved(template: CommandTemplate) -> bool:
    """检查命令模板中的所有占位符是否已被替换

    未替换的占位符会阻止执行（SECURITY_CHECKING 将报 CRITICAL）。
    """
    remaining = PLACEHOLDER_PATTERN.findall(template.command)
    return len(remaining) == 0


# ===== 步骤操作 =====


def remove_step(
    commands: list[CommandTemplate],
    index: int,
) -> list[CommandTemplate]:
    """删除指定步骤

    Args:
        commands: 命令模板列表
        index: 要删除的步骤索引（0-based）

    Returns:
        删除后的新列表

    Raises:
        IndexError: 索引越界
    """
    if index < 0 or index >= len(commands):
        raise IndexError(f"步骤索引 {index} 越界，共 {len(commands)} 步")
    return [c for i, c in enumerate(commands) if i != index]


def reorder_steps(
    commands: list[CommandTemplate],
    new_order: list[int],
) -> list[CommandTemplate]:
    """按指定顺序重排步骤

    Args:
        commands: 命令模板列表
        new_order: 新顺序的索引列表（如 [2, 0, 1] 表示原第3步→第1步→原第2步）

    Returns:
        重排后的新列表

    Raises:
        ValueError: new_order 不是有效排列
    """
    if sorted(new_order) != list(range(len(commands))):
        raise ValueError(f"new_order 不是有效排列: {new_order}")
    return [commands[i] for i in new_order]
