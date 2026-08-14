"""受控执行器（EXECUTING 步骤）

按步骤执行修复命令并监控：逐步执行 + 失败即停 + 错误捕获 + 超时控制。
执行失败时不继续后续步骤（对齐任务书 REQ-D-02 验收标准第 2 条）。

失败自动触发回滚由 engine.py 负责（调用 snapshot.rollback()），
本模块只负责执行和返回结果。

对齐 Safety_design.md §受控执行器设计。
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from galaxy_diag.shared.types import ExecuteResult

if TYPE_CHECKING:
    from galaxy_diag.shared.types import FixProposal


# 单条命令执行超时（秒）
_COMMAND_TIMEOUT = 300

# 验证步骤（只读）超时较短
_VERIFY_TIMEOUT = 60


def run(proposal: "FixProposal", *, dry_run: bool = False) -> ExecuteResult:
    """受控执行修复 (EXECUTING)

    逐步执行 proposal.commands 中的命令，失败即停。

    Args:
        proposal: 待执行的修复建议
        dry_run: 干跑模式（只打印不执行，用于测试）

    Returns:
        ExecuteResult: 执行结果，含成功标志、输出、失败步骤序号

    Note:
        本模块执行**命令列表**（proposal.commands），不直接执行脚本
        （proposal.script），因为脚本执行的风险评估由 E-02 已完成，
        且逐命令执行便于失败定位和回滚。
    """
    if not proposal.commands:
        return ExecuteResult(success=True, output="无可执行的命令")

    output_lines: list[str] = []

    for idx, cmd in enumerate(proposal.commands, 1):
        # 验证步骤使用较短超时
        timeout = _VERIFY_TIMEOUT if cmd.is_verification else _COMMAND_TIMEOUT

        prefix = f"[验证 {idx}]" if cmd.is_verification else f"[步骤 {idx}]"
        output_lines.append(f"{prefix} 执行: {cmd.command}")

        if dry_run:
            output_lines.append(f"  (dry-run 跳过实际执行)")
            continue

        try:
            result = subprocess.run(
                cmd.command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            if result.stdout:
                output_lines.append(result.stdout.rstrip())
            if result.stderr:
                output_lines.append(f"  [stderr] {result.stderr.rstrip()}")

            if result.returncode != 0:
                # 失败即停：不继续执行后续步骤
                output_lines.append(
                    f"  ✗ 命令失败（退出码 {result.returncode}），停止执行后续步骤"
                )
                return ExecuteResult(
                    success=False,
                    output="\n".join(output_lines),
                    failed_step=idx,
                )

            output_lines.append(f"  ✓ 成功")

        except subprocess.TimeoutExpired:
            output_lines.append(f"  ✗ 命令超时（{timeout}s），停止执行后续步骤")
            return ExecuteResult(
                success=False,
                output="\n".join(output_lines),
                failed_step=idx,
            )
        except Exception as e:
            output_lines.append(f"  ✗ 执行异常: {e}，停止执行后续步骤")
            return ExecuteResult(
                success=False,
                output="\n".join(output_lines),
                failed_step=idx,
            )

    return ExecuteResult(
        success=True,
        output="\n".join(output_lines),
        failed_step=-1,
    )
