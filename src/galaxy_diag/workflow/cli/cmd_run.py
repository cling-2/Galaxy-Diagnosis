"""galaxy-diag run — 端到端工作流

对应需求: REQ-F-02
当前为 stub：展示框架可用。
完整实现时将编排：信息收集 → 环境识别 → 根因分析 → 修复建议 → 人工审核 → 执行 → 验证
"""

from __future__ import annotations

import argparse

from galaxy_diag.shared.types import WorkflowStep
from galaxy_diag.workflow.cli.display import get_console, print_header, print_stub_notice


def register(subparsers: argparse._SubParsersAction) -> None:
    sub = subparsers.add_parser(
        "run",
        help="端到端工作流 (REQ-F-02)",
        description="完整诊断-修复流程编排: 采集 → 识别 → 分析 → 修复 → 审核 → 执行 → 验证",
    )
    sub.add_argument(
        "--description", "-d",
        metavar="TEXT",
        help="问题描述",
    )
    sub.add_argument(
        "--resume",
        metavar="ID",
        help="恢复中断的工作流",
    )
    sub.set_defaults(callback=handle)


def handle(args: argparse.Namespace) -> None:
    """run 子命令回调"""
    console = get_console()
    print_header()

    # 如果指定 --resume，展示恢复提示
    if args.resume:
        console.print(f"[info]恢复工作流会话: {args.resume}[/info]\n")
    else:
        console.print("[heading]启动诊断-修复工作流[/heading]\n")

    # 打印 stub 提示
    print_stub_notice("REQ-F-02", "端到端工作流编排")

    # 展示工作流步骤概览
    console.print("\n[heading]工作流步骤:[/heading]")
    step_descriptions = {
        WorkflowStep.COLLECT: "信息收集 — 采集系统环境与日志",
        WorkflowStep.DIAGNOSE: "根因分析 — 基于诊断信息推理",
        WorkflowStep.FIX: "修复生成 — 生成修复命令/脚本",
        WorkflowStep.REVIEW: "人工审核 — 安全确认后执行",
        WorkflowStep.EXECUTE: "执行修复 — 按步骤执行并监控",
        WorkflowStep.VERIFY: "结果验证 — 确认修复生效",
    }
    for i, (step, desc) in enumerate(step_descriptions.items(), 1):
        console.print(f"  [dim]{i}.[/dim] [info]{step.value:8s}[/info] {desc}")

    console.print("")
