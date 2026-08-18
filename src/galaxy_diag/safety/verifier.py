"""结果验证器（VERIFYING 步骤）

执行修复建议中的验证命令（is_verification=True），判定修复是否生效。
与 safety/executor.py 对称：executor 执行修复命令，verifier 执行验证命令。

验证策略：纯规则判定——逐条执行验证命令，全部退出码=0 → 修复生效，
任一非零 → 修复未生效。不经 LLM。

对齐设计文档 §3 验证器设计。
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from galaxy_diag.shared.types import VerifyResult

if TYPE_CHECKING:
    from galaxy_diag.shared.types import FixProposal


# 验证命令超时（秒）——验证命令均为只读操作，超时较短
_VERIFY_TIMEOUT = 60


def verify(proposal: "FixProposal", *, dry_run: bool = False) -> VerifyResult:
    """执行验证命令并判定修复是否生效 (VERIFYING)

    从 proposal.commands 中筛选 is_verification=True 的命令，
    逐条 subprocess 执行（只读操作、短超时 60s），
    全部退出码=0 → success=True，
    任一非零 → success=False（附带失败步骤信息）。

    无验证命令时 → 自动判定 success=True（保守策略：
    修复命令已全部成功执行，无验证手段时视为通过，
    由 engine 层展示"未执行验证"提示）。

    Args:
        proposal: 修复建议（含验证命令）
        dry_run: 干跑模式（只打印不执行，用于测试）

    Returns:
        VerifyResult: 验证结果

    不经 LLM，纯 subprocess 退出码判定。
    """
    # 筛选验证命令（分离 requires_host 命令，不在本机执行）
    verify_cmds = [cmd for cmd in proposal.commands if cmd.is_verification and not cmd.requires_host]
    host_cmds = [cmd for cmd in proposal.commands if cmd.is_verification and cmd.requires_host]

    # 所有验证命令都需在宿主机执行：无法本机验证，提示人工确认
    if not verify_cmds and host_cmds:
        return VerifyResult(
            success=True,
            output="所有验证命令需在宿主机执行，无法在本机自动验证",
            total_steps=0,
            passed_steps=0,
            host_required_commands=[c.command for c in host_cmds],
        )

    # 无验证命令：保守通过，由 engine 层额外提示
    if not verify_cmds and not host_cmds:
        return VerifyResult(
            success=True,
            output="无验证命令，修复步骤已全部执行完毕",
            total_steps=0,
            passed_steps=0,
        )

    output_lines: list[str] = []
    passed_count = 0

    # 提示需宿主机执行的验证命令
    if host_cmds:
        output_lines.append("[需宿主机执行] 以下验证命令不在本机执行:")
        for hc in host_cmds:
            output_lines.append(f"  - {hc.command}  ({hc.description})")

    for idx, cmd in enumerate(verify_cmds, 1):
        output_lines.append(f"[验证 {idx}/{len(verify_cmds)}] 执行: {cmd.command}")

        if dry_run:
            output_lines.append("  (dry-run 跳过实际执行)")
            passed_count += 1
            continue

        try:
            result = subprocess.run(
                cmd.command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=_VERIFY_TIMEOUT,
            )

            if result.stdout:
                output_lines.append(result.stdout.rstrip())
            if result.stderr:
                output_lines.append(f"  [stderr] {result.stderr.rstrip()}")

            if result.returncode != 0:
                # 失败即停
                output_lines.append(
                    f"  ✗ 验证失败（退出码 {result.returncode}），停止后续验证"
                )
                return VerifyResult(
                    success=False,
                    output="\n".join(output_lines),
                    failed_step=idx,
                    failed_description=cmd.description,
                    total_steps=len(verify_cmds),
                    passed_steps=passed_count,
                    host_required_commands=[c.command for c in host_cmds],
                )

            output_lines.append("  ✓ 验证通过")
            passed_count += 1

        except subprocess.TimeoutExpired:
            output_lines.append(f"  ✗ 验证命令超时（{_VERIFY_TIMEOUT}s），停止后续验证")
            return VerifyResult(
                success=False,
                output="\n".join(output_lines),
                failed_step=idx,
                failed_description=cmd.description,
                total_steps=len(verify_cmds),
                passed_steps=passed_count,
                host_required_commands=[c.command for c in host_cmds],
            )
        except Exception as e:
            output_lines.append(f"  ✗ 验证异常: {e}，停止后续验证")
            return VerifyResult(
                success=False,
                output="\n".join(output_lines),
                failed_step=idx,
                failed_description=cmd.description,
                total_steps=len(verify_cmds),
                passed_steps=passed_count,
            )

    # 全部通过
    return VerifyResult(
        success=True,
        output="\n".join(output_lines),
        total_steps=len(verify_cmds),
        passed_steps=passed_count,
        host_required_commands=[c.command for c in host_cmds],
    )
