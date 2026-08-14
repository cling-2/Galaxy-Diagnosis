"""galaxy-diag audit-log — 审计日志查询

对应需求: REQ-E-04
调用 safety.audit.query_audit 查询审计日志并渲染。
"""

from __future__ import annotations

import argparse
from datetime import datetime

from galaxy_diag.safety import audit as audit_mod
from galaxy_diag.workflow.cli.display import get_console, print_audit_records


def register(subparsers: argparse._SubParsersAction) -> None:
    sub = subparsers.add_parser(
        "audit-log",
        help="审计日志查询 (REQ-E-04)",
        description="查询操作留痕记录（审计日志）",
    )
    sub.add_argument(
        "--session",
        "-s",
        dest="session_id",
        help="按会话 ID 过滤",
    )
    sub.add_argument(
        "--limit",
        "-n",
        type=int,
        default=50,
        help="最多返回记录数（默认 50）",
    )
    sub.add_argument(
        "--since",
        help="只返回此时间之后的记录（ISO 格式，如 2026-08-14）",
    )
    sub.set_defaults(func=cmd_audit_log)


def cmd_audit_log(args: argparse.Namespace) -> None:
    """查询审计日志"""
    console = get_console()

    since_dt: datetime | None = None
    if args.since:
        try:
            # 支持日期和完整时间戳
            for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                try:
                    since_dt = datetime.strptime(args.since, fmt)
                    break
                except ValueError:
                    continue
            if since_dt is None:
                # 尝试 ISO 格式（含时区等）
                since_dt = datetime.fromisoformat(args.since)
        except ValueError:
            console.print(f"[danger]无效的时间格式: {args.since}[/danger]")
            console.print("[dim]  支持格式: 2026-08-14 或 2026-08-14 12:00:00[/dim]")
            return

    records = audit_mod.query_audit(
        session_id=args.session_id,
        limit=args.limit,
        since=since_dt,
    )

    if not records:
        console.print("[dim]暂无审计日志记录[/dim]")
        return

    print_audit_records(records)
