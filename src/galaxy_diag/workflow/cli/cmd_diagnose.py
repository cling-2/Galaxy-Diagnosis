"""galaxy-diag diagnose — 问题诊断

对应需求: REQ-C-01 / REQ-C-02 / REQ-C-03
当前为 stub：展示框架可用 + 示例输出格式。
"""

from __future__ import annotations

import argparse

from galaxy_diag.shared.types import Confidence, DiagnosisResult, EnvironmentType
from galaxy_diag.workflow.cli.display import get_console, print_diagnosis, print_stub_notice


def register(subparsers: argparse._SubParsersAction) -> None:
    sub = subparsers.add_parser(
        "diagnose",
        help="问题诊断 (REQ-C)",
        description="信息收集编排、根因分析、不确定性声明",
    )
    sub.add_argument(
        "--description", "-d",
        metavar="TEXT",
        help="问题描述（交互式输入的替代方式）",
    )
    sub.add_argument(
        "--session",
        metavar="ID",
        help="继续已有诊断会话",
    )
    sub.add_argument(
        "--output",
        choices=["table", "json"],
        default="table",
        help="输出格式 (默认: table)",
    )
    sub.set_defaults(callback=handle)


def handle(args: argparse.Namespace) -> None:
    """diagnose 子命令回调"""
    console = get_console()

    # 打印 stub 提示
    print_stub_notice("REQ-C", "诊断分析")

    # 展示示例输出格式
    console.print("\n[dim]--- 以下为示例输出（mock 数据）---[/dim]\n")

    mock_result = DiagnosisResult(
        root_cause="VM 磁盘控制器驱动 `vmw_pvscsi` 未加载，导致 SCSI 设备不可见",
        confidence=Confidence.SUSPECTED,
        evidence=[
            "lsblk 仅显示系统盘 sda",
            "dmesg 中发现 'pvscsi: unknown device' 警告",
            "VM 硬件配置使用 VMware 半虚拟化 SCSI 控制器",
        ],
        missing_info=[
            "VM 硬件版本信息",
            "内核完整版本号（判断驱动是否内置）",
        ],
        env_type=EnvironmentType.VM,
    )

    print_diagnosis(mock_result)
