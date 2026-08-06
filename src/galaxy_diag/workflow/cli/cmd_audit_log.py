"""galaxy-diag audit-log — 审计日志查询

对应需求: REQ-E-04
当前为 stub：展示框架可用 + 示例输出格式。
"""

from __future__ import annotations

import argparse
from datetime import datetime

from galaxy_diag.shared.types import AuditRecord
from galaxy_diag.workflow.cli.display import get_console, print_audit_records, print_stub_notice


def register(subparsers: argparse._SubParsersAction) -> None:
    sub = subparsers.add_parser(
        "audit-log",
        help="审计日志查询 (REQ-E-04)",
        description="查询操作审计日志，所有操作可追溯",
    )
    sub.add_argument(
        "--session",
        metavar="ID",
        help="按会话筛选",
    )
    sub.add_argument(
        "--limit",
        type=int,
        default=20,
        metavar="N",
        help="显示最近 N 条 (默认: 20)",
    )
    sub.add_argument(
        "--since",
        metavar="DATETIME",
        help="起始时间 (如 '2026-08-05 14:00:00')",
    )
    sub.set_defaults(callback=handle)


def handle(args: argparse.Namespace) -> None:
    """audit-log 子命令回调"""
    console = get_console()

    # 打印 stub 提示
    print_stub_notice("REQ-E-04", "审计日志查询")

    # mock 数据
    mock_records = [
        AuditRecord(
            timestamp=datetime(2026, 8, 5, 14, 30, 0),
            session_id="sess_20260805_001",
            operator="admin",
            action="modprobe vmw_pvscsi",
            result="success",
            llm_basis="基于 dmesg 日志中 pvscsi 设备未识别的警告",
            snapshot_id="snap_20260805_001",
            user_input="y",
        ),
        AuditRecord(
            timestamp=datetime(2026, 8, 5, 15, 10, 0),
            session_id="sess_20260805_001",
            operator="admin",
            action="mount -t ext4 /dev/sdb1 /data",
            result="rejected",
            llm_basis="诊断结论为推测，磁盘设备路径未确认",
            snapshot_id=None,
            user_input="N",
        ),
        AuditRecord(
            timestamp=datetime(2026, 8, 5, 15, 12, 0),
            session_id="sess_20260805_001",
            operator="admin",
            action="mount -t ext4 /dev/sdb1 /data",
            result="success",
            llm_basis="二次确认后用户手动修正设备路径为 /dev/sdc1",
            snapshot_id="snap_20260805_002",
            user_input="y",
        ),
    ]

    console.print("\n[dim]--- 以下为示例输出（mock 数据）---[/dim]\n")
    print_audit_records(mock_records)
