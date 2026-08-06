"""galaxy-diag review — 审核确认

对应需求: REQ-E-01 / REQ-F-03
当前为 stub：展示框架可用。
"""

from __future__ import annotations

import argparse

from galaxy_diag.workflow.cli.display import get_console, print_stub_notice


def register(subparsers: argparse._SubParsersAction) -> None:
    sub = subparsers.add_parser(
        "review",
        help="审核确认 (REQ-E/F-03)",
        description="查看待审核操作摘要，确认/拒绝/修改修复建议",
    )
    sub.add_argument(
        "--session",
        metavar="ID",
        required=False,
        help="诊断会话 ID",
    )
    sub.add_argument(
        "--step",
        type=int,
        metavar="NUMBER",
        help="审核指定步骤编号",
    )
    sub.set_defaults(callback=handle)


def handle(args: argparse.Namespace) -> None:
    """review 子命令回调"""
    console = get_console()
    print_stub_notice("REQ-E / REQ-F-03", "审核确认")

    # 演示确认交互
    from galaxy_diag.workflow.cli.interact import confirm
    console.print("\n[info]确认交互演示:[/info]")
    result = confirm("是否确认执行修复操作?", default=False)
    if result:
        console.print("[success]✓ 用户已确认[/success]")
    else:
        console.print("[dim]  用户已拒绝[/dim]")
