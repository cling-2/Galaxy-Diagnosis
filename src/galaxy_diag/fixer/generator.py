"""脚本生成器（REQ-D-02）

将多条修复命令组装为含错误处理逻辑的可执行脚本。
确定性纯函数——不依赖 LLM、不修改状态。

对应设计文档 §脚本生成器。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from galaxy_diag.shared.types import CommandTemplate

# ===== Bash 脚本模板 =====

_BASH_HEADER = """\
#!/usr/bin/env bash
# 银河平台部署问题修复脚本
# 由 galaxy-diag 自动生成，请执行前仔细审核
# 生成时间: {timestamp}
# 诊断依据: {root_cause}

set -euo pipefail

log_step() {{
    echo "[步骤 $1/$2] $3"
}}

log_error() {{
    echo "[错误] $1" >&2
}}
"""

_BASH_STEP_TEMPLATE = """
log_step {step_num} {total_steps} "{description}"
# 风险提示: {risk_note}
{command}
"""

_BASH_FOOTER = """
echo "[完成] 所有修复步骤已执行"
"""

# ===== Python 脚本模板 =====

_PYTHON_HEADER = """\
#!/usr/bin/env python3
\"\"\"银河平台部署问题修复脚本
由 galaxy-diag 自动生成，请执行前仔细审核
诊断依据: {root_cause}
\"\"\"

import subprocess
import sys


def run_step(description: str, command: str) -> None:
    print(f"[步骤] {{description}}")
    result = subprocess.run(command, shell=True, check=False)
    if result.returncode != 0:
        print(f"[错误] 命令失败 (退出码 {{result.returncode}}): {{command}}", file=sys.stderr)
        sys.exit(result.returncode)

"""

_PYTHON_STEP_TEMPLATE = """
# 风险提示: {risk_note}
run_step(
    description="{description}",
    command="{command}",
)

"""

_PYTHON_FOOTER = """
print("[完成] 所有修复步骤已执行")
"""


# ===== 生成函数 =====


def generate_bash_script(
    commands: list[CommandTemplate],
    root_cause: str = "",
    timestamp: str | None = None,
) -> str:
    """将命令列表组装为含错误处理的 Bash 脚本

    set -euo pipefail:
      -e: 任一命令失败时立即终止（D-02: 某步骤失败时不继续执行）
      -u: 引用未定义变量时报错（防止占位符未替换导致的空变量）
      -o pipefail: 管道中任一命令失败时整个管道失败
    """
    total = len(commands)
    parts: list[str] = []

    ts = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    parts.append(_BASH_HEADER.format(
        timestamp=ts,
        root_cause=root_cause[:200],
    ))

    for i, cmd in enumerate(commands, 1):
        parts.append(_BASH_STEP_TEMPLATE.format(
            step_num=i,
            total_steps=total,
            description=cmd.description.replace('"', '\\"'),
            risk_note=cmd.risk_note.replace('"', '\\"'),
            command=cmd.command,
        ))

    parts.append(_BASH_FOOTER)
    return "".join(parts)


def generate_python_script(
    commands: list[CommandTemplate],
    root_cause: str = "",
) -> str:
    """将命令列表组装为含错误处理的 Python 脚本"""
    parts: list[str] = []
    parts.append(_PYTHON_HEADER.format(root_cause=root_cause[:200]))

    for cmd in commands:
        parts.append(_PYTHON_STEP_TEMPLATE.format(
            description=cmd.description.replace('"', '\\"'),
            risk_note=cmd.risk_note.replace('"', '\\"'),
            command=cmd.command.replace('"', '\\"'),
        ))

    parts.append(_PYTHON_FOOTER)
    return "".join(parts)


def generate_script(
    commands: list[CommandTemplate],
    language: str = "bash",
    root_cause: str = "",
    timestamp: str | None = None,
) -> str:
    """统一脚本生成入口

    Args:
        commands: 命令模板列表（不含验证步骤，由调用方过滤）
        language: 脚本语言 "bash" 或 "python"
        root_cause: 诊断根因（写入脚本头部注释）
        timestamp: 可选时间戳（默认 datetime.now()，传入可使测试确定性）

    Returns:
        生成的脚本内容
    """
    if language == "python":
        return generate_python_script(commands, root_cause)
    return generate_bash_script(commands, root_cause, timestamp)
