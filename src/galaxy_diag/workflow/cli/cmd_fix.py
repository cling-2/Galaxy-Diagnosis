"""galaxy-diag fix — 修复建议查看/编辑

对应需求: REQ-D-01 / REQ-D-02 / REQ-D-03
当前为 stub：展示框架可用 + 示例输出格式。
"""

from __future__ import annotations

import argparse

from galaxy_diag.shared.types import CommandTemplate, FixProposal
from galaxy_diag.workflow.cli.display import get_console, print_fix_proposal, print_stub_notice


def register(subparsers: argparse._SubParsersAction) -> None:
    sub = subparsers.add_parser(
        "fix",
        help="修复建议查看/编辑 (REQ-D)",
        description="查看修复命令建议、交互式编辑参数、生成修复脚本",
    )
    sub.add_argument(
        "--session",
        metavar="ID",
        required=False,
        help="诊断会话 ID",
    )
    sub.add_argument(
        "--edit",
        action="store_true",
        help="交互式编辑修复参数",
    )
    sub.add_argument(
        "--generate-script",
        action="store_true",
        help="生成多步骤修复脚本",
    )
    sub.add_argument(
        "--output",
        choices=["table", "script"],
        default="table",
        help="输出格式 (默认: table)",
    )
    sub.set_defaults(callback=handle)


def handle(args: argparse.Namespace) -> None:
    """fix 子命令回调"""
    console = get_console()

    # 打印 stub 提示
    print_stub_notice("REQ-D", "修复生成")

    # 如果指定 --edit，演示交互式参数编辑
    if args.edit:
        from galaxy_diag.workflow.cli.interact import prompt_edit_params
        console.print("\n[info]交互式参数编辑演示:[/info]")
        result = prompt_edit_params(
            template="mount -t <FS_TYPE> <DEVICE> <MOUNT_POINT>",
            placeholders={
                "FS_TYPE": "ext4",
                "DEVICE": "/dev/sdb1",
                "MOUNT_POINT": "/data",
            },
        )
        console.print(f"\n[success]✓ 参数已确认: {result}[/success]")
        return

    # 展示示例输出格式
    console.print("\n[dim]--- 以下为示例输出（mock 数据）---[/dim]\n")

    mock_proposal = FixProposal(
        commands=[
            CommandTemplate(
                command="modprobe <DRIVER_MODULE>",
                description="加载磁盘控制器驱动模块",
                risk_note="加载内核模块",
                editable_params={"DRIVER_MODULE": "vmw_pvscsi"},
            ),
            CommandTemplate(
                command="rescan-scsi-bus.sh",
                description="重新扫描 SCSI 总线",
                risk_note="无",
                editable_params={},
            ),
            CommandTemplate(
                command="lsblk",
                description="验证磁盘是否可见",
                risk_note="只读操作",
                editable_params={},
            ),
        ],
        script="""#!/bin/bash
set -euo pipefail

echo "=== Galaxy-Diag 修复脚本 ==="

# 1. 加载驱动
modprobe vmw_pvscsi
if [ $? -ne 0 ]; then
    echo "ERROR: 驱动加载失败" >&2
    exit 1
fi

# 2. 重新扫描 SCSI 总线
rescan-scsi-bus.sh

# 3. 验证
sleep 2
if lsblk | grep -q sdb; then
    echo "SUCCESS: 数据磁盘 sdb 已识别"
else
    echo "WARNING: 数据磁盘仍未出现，可能需要手动检查" >&2
fi
""",
        script_language="bash",
        risk_notes=["加载内核模块可能影响系统稳定性"],
        check_passed=True,
        check_issues=[],
        impact_scope="加载内核模块 vmw_pvscsi，扫描 SCSI 总线",
    )

    print_fix_proposal(mock_proposal)
