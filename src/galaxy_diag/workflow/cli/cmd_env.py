"""galaxy-diag env — 环境识别 & 硬件采集

对应需求: REQ-B-01 / REQ-B-02
当前为 stub：展示框架可用 + 示例输出格式。
"""

from __future__ import annotations

import argparse

from galaxy_diag.shared.types import EnvInfo, EnvironmentType, HardwareInfo, StorageInfo
from galaxy_diag.workflow.cli.display import get_console, print_env_info, print_stub_notice


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

    # 打印 stub 提示
    print_stub_notice("REQ-B", "环境识别与硬件采集")

    # 展示示例输出格式（使用 mock 数据演示 Rich 渲染）
    console.print("\n[dim]--- 以下为示例输出（mock 数据）---[/dim]\n")

    mock_env = EnvInfo(
        env_type=EnvironmentType.VM,
        hardware=HardwareInfo(
            cpu_model="Intel Xeon E5-2680 v4",
            cpu_cores=4,
            memory_total_gb=16.0,
            disks=[
                {"type": "SSD", "capacity": "100GB", "model": "sda"},
                {"type": "HDD", "capacity": "500GB", "model": "sdb"},
            ],
            raid_cards=[],
            nics=[
                {"model": "virtio-net", "driver": "virtio_pci"},
            ],
        ),
        storage=[
            StorageInfo(
                storage_type="NAS",
                mount_path="/mnt/data",
                filesystem="nfs4",
                details={"server": "nas-01.internal"},
            ),
        ],
    )

    print_env_info(mock_env, format=args.output)
