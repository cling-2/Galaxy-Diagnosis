"""galaxy-diag env — 环境识别 & 硬件采集

对应需求: REQ-B-01 / REQ-B-02
"""

from __future__ import annotations

import argparse

from galaxy_diag.collector import collect_env
from galaxy_diag.collector.env_detect import EnvironmentDetector
from galaxy_diag.shared.constants import ENV_TYPE_LABELS
from galaxy_diag.shared.errors import GalaxyDiagError
from galaxy_diag.workflow.cli.display import get_console, print_env_info


def register(subparsers: argparse._SubParsersAction) -> None:
    sub = subparsers.add_parser(
        "env",
        help="环境识别 & 硬件采集 (REQ-B)",
        description="自动识别运行环境类型并采集异构软硬件信息",
    )
    sub.add_argument(
        "--type-only",
        action="store_true",
        help="仅输出环境类型，不采集硬件详情",
    )
    sub.add_argument(
        "--output",
        choices=["table", "json", "yaml"],
        default="table",
        help="输出格式 (默认: table)",
    )
    sub.set_defaults(callback=handle)


def handle(args: argparse.Namespace) -> None:
    """env 子命令回调"""
    console = get_console()

    try:
        if args.type_only:
            warnings: list[str] = []
            env_type = EnvironmentDetector().detect(warnings)
            env_label = ENV_TYPE_LABELS.get(env_type, str(env_type))
            console.print(f"[heading]🔍 环境识别结果[/heading]")
            console.print(f"  环境类型: [info]{env_label}[/info]")
            if warnings:
                console.print("\n[warning]⚠ 采集提示[/warning]")
                for w in warnings:
                    console.print(f"  - {w}")
            return

        env_info = collect_env()
        print_env_info(env_info, format=args.output)
    except GalaxyDiagError as e:
        console.print(f"[danger]✗ {e.message}[/danger]")
        if e.hint:
            console.print(f"  💡 {e.hint}")
