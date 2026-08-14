"""galaxy-diag snapshot — 快照管理/回滚

对应需求: REQ-E-03
调用 safety.snapshot 模块列出快照、展示详情、执行回滚。
"""

from __future__ import annotations

import argparse

from galaxy_diag.safety import snapshot as snapshot_mod
from galaxy_diag.shared.errors import GalaxyDiagError
from galaxy_diag.workflow.cli.display import (
    get_console,
    print_snapshot_list,
    print_snapshot_meta,
)
from galaxy_diag.workflow.cli.interact import confirm


def register(subparsers: argparse._SubParsersAction) -> None:
    sub = subparsers.add_parser(
        "snapshot",
        help="快照管理/回滚 (REQ-E-03)",
        description="查看快照列表、展示快照详情、执行一键回滚",
    )
    snapshot_sub = sub.add_subparsers(
        dest="snapshot_action",
        title="快照操作",
        required=True,
    )

    # list
    sub_list = snapshot_sub.add_parser("list", help="列出所有快照")
    sub_list.set_defaults(func=cmd_list)

    # show
    sub_show = snapshot_sub.add_parser("show", help="展示快照详情")
    sub_show.add_argument("snapshot_id", help="快照 ID")
    sub_show.set_defaults(func=cmd_show)

    # rollback
    sub_rb = snapshot_sub.add_parser("rollback", help="一键回滚到快照")
    sub_rb.add_argument("snapshot_id", help="快照 ID")
    sub_rb.set_defaults(func=cmd_rollback)


def cmd_list(args: argparse.Namespace) -> None:
    """列出所有快照"""
    console = get_console()
    metas = snapshot_mod.list_snapshots()
    if not metas:
        console.print("[dim]暂无快照记录[/dim]")
        return
    print_snapshot_list(metas)


def cmd_show(args: argparse.Namespace) -> None:
    """展示快照详情"""
    console = get_console()
    metas = snapshot_mod.list_snapshots()
    target = next((m for m in metas if m.snapshot_id == args.snapshot_id), None)
    if target is None:
        console.print(f"[danger]快照不存在: {args.snapshot_id}[/danger]")
        return
    print_snapshot_meta(target)


def cmd_rollback(args: argparse.Namespace) -> None:
    """一键回滚（需人工审核，红线 2）"""
    console = get_console()
    console.print(f"\n[warning]即将回滚到快照: {args.snapshot_id}[/warning]")
    console.print("[dim]此操作将恢复受影响的配置文件并重启相关服务[/dim]")

    # 回滚操作本身需经 REQ-E-01 人工审核（危险操作，要求输入 CONFIRM）
    if not confirm("确认回滚? 此操作将恢复到快照时的状态", default=False, danger=True):
        console.print("[dim]  回滚已取消[/dim]")
        return

    try:
        result = snapshot_mod.rollback(args.snapshot_id)
        if result.success:
            console.print(f"[success]✓ 回滚成功: {result.message}[/success]")
        else:
            console.print(f"[warning]⚠ 回滚部分完成: {result.message}[/warning]")
            console.print("[dim]  请人工检查系统状态[/dim]")
    except GalaxyDiagError as e:
        console.print(f"[danger]✗ {e.message}[/danger]")
        if e.hint:
            console.print(f"  💡 {e.hint}")
