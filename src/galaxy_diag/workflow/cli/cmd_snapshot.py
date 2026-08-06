"""galaxy-diag snapshot — 快照管理/回滚

对应需求: REQ-E-03
当前为 stub：展示框架可用 + 示例输出格式。
"""

from __future__ import annotations

import argparse
from datetime import datetime

from galaxy_diag.shared.types import SnapshotMeta
from galaxy_diag.workflow.cli.display import (
    get_console,
    print_snapshot_list,
    print_snapshot_meta,
    print_stub_notice,
)


def register(subparsers: argparse._SubParsersAction) -> None:
    sub = subparsers.add_parser(
        "snapshot",
        help="快照管理/回滚 (REQ-E-03)",
        description="查看快照列表、快照详情、一键回滚",
    )
    sub.add_argument(
        "action",
        choices=["list", "show", "rollback"],
        help="快照操作: list=列出, show=查看详情, rollback=回滚",
    )
    sub.add_argument(
        "snapshot_id",
        nargs="?",
        help="快照 ID (show/rollback 需要)",
    )
    sub.add_argument(
        "--session",
        metavar="ID",
        help="按会话筛选",
    )
    sub.set_defaults(callback=handle)


def handle(args: argparse.Namespace) -> None:
    """snapshot 子命令回调"""
    console = get_console()

    # 打印 stub 提示
    print_stub_notice("REQ-E-03", "操作快照与回滚")

    # mock 数据
    mock_snapshots = [
        SnapshotMeta(
            snapshot_id="snap_20260805_001",
            timestamp=datetime(2026, 8, 5, 14, 30, 0),
            operation_summary="modprobe vmw_pvscsi && rescan-scsi-bus.sh",
            affected_files=["/etc/modprobe.d/vmw_pvscsi.conf"],
            affected_services=[],
            backup_path="~/.galaxy-diag/snapshots/snap_20260805_001",
        ),
        SnapshotMeta(
            snapshot_id="snap_20260805_002",
            timestamp=datetime(2026, 8, 5, 15, 10, 0),
            operation_summary="mount -t ext4 /dev/sdb1 /data",
            affected_files=["/etc/fstab"],
            affected_services=["data-mount.service"],
            backup_path="~/.galaxy-diag/snapshots/snap_20260805_002",
        ),
    ]

    console.print("\n[dim]--- 以下为示例输出（mock 数据）---[/dim]\n")

    if args.action == "list":
        print_snapshot_list(mock_snapshots)

    elif args.action == "show":
        snap_id = args.snapshot_id or mock_snapshots[0].snapshot_id
        # 查找匹配的 mock 快照
        target = next(
            (s for s in mock_snapshots if s.snapshot_id == snap_id),
            mock_snapshots[0],
        )
        print_snapshot_meta(target)

    elif args.action == "rollback":
        snap_id = args.snapshot_id or mock_snapshots[-1].snapshot_id
        console.print(f"[info]回滚到快照: {snap_id}[/info]")
        from galaxy_diag.workflow.cli.interact import confirm
        if confirm("确认回滚? 此操作将恢复到快照时的状态", default=False, danger=True):
            console.print("[success]✓ 回滚已确认（实际回滚逻辑在 REQ-E-03 实现）[/success]")
        else:
            console.print("[dim]  回滚已取消[/dim]")
