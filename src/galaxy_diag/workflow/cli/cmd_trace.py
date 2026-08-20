"""galaxy-diag trace — 推理链路查询与展示

对应需求: REQ-X-04
调用 trace.viewer 加载 JSONL trace 并 Rich 树形渲染。
"""

from __future__ import annotations

import argparse

from galaxy_diag.trace import viewer
from galaxy_diag.workflow.cli.display import get_console


def register(subparsers: argparse._SubParsersAction) -> None:
    sub = subparsers.add_parser(
        "trace",
        help="推理链路查看 (REQ-X-04)",
        description="查看指定诊断任务的推理过程（Agent Trace）",
    )
    sub.add_argument(
        "session_id",
        help="诊断会话 ID",
    )
    sub.add_argument(
        "--step",
        "-s",
        help="按步骤过滤（如 DIAGNOSING / COLLECTING / REVIEWING）",
    )
    sub.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="显示完整 completion / output_summary 等详细内容",
    )
    sub.set_defaults(callback=cmd_trace)


def cmd_trace(args: argparse.Namespace) -> None:
    """查看推理链路"""
    console = get_console()

    tree = viewer.load_trace(args.session_id)
    if tree is None:
        console.print(
            f"[warning]未找到会话 {args.session_id} 的推理链路记录[/warning]"
        )
        console.print(
            "[dim]  trace 文件位于 ~/.galaxy-diag/traces/<session_id>.jsonl[/dim]"
        )
        console.print("[dim]  请确认会话 ID 正确，或先运行 galaxy-diag run 完成诊断[/dim]")
        return

    if not tree.spans:
        console.print(f"[warning]会话 {args.session_id} 的 trace 为空[/warning]")
        return

    viewer.render(
        tree,
        step_filter=args.step,
        verbose=args.verbose,
        console=console,
    )
