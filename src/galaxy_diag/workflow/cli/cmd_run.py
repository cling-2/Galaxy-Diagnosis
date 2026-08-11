"""galaxy-diag run — 端到端工作流

对应需求: REQ-F-02
通过 WorkflowEngine 编排：环境识别 → 信息采集 → 根因分析 → 修复建议 → 安全检测 → 人工审核 → 执行 → 验证
"""

from __future__ import annotations

import argparse

from galaxy_diag.shared.errors import WorkflowError
from galaxy_diag.workflow.cli import interact
from galaxy_diag.workflow.cli.display import get_console, print_header
from galaxy_diag.workflow.engine import WorkflowEngine
from galaxy_diag.workflow.persist import find_resumable_sessions
from galaxy_diag.workflow.states import STEP_LABELS


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
        nargs="?",
        const="latest",
        metavar="ID",
        help="恢复中断的工作流；不指定 ID 时默认恢复最近的未完成会话",
    )
    sub.add_argument(
        "--auto",
        action="store_true",
        help="自动模式（中间步骤只展示不暂停，审核步骤仍需人工）",
    )
    sub.add_argument(
        "--log-file",
        action="append",
        metavar="PATH",
        dest="log_files",
        help="上传日志文件供诊断参考（可多次指定）",
    )
    sub.set_defaults(callback=handle)


def _resolve_resume_id(resume_arg: str | None, console: Console) -> str | None:
    """解析 --resume 参数，返回实际的 session_id 或 None

    - --resume ID  → 直接返回 ID
    - --resume (无 ID) → 查找最近的未完成会话，自动恢复
    - 未指定 --resume → 返回 None
    """
    if resume_arg is None:
        return None

    # 用户给了显式 ID
    if resume_arg != "latest":
        return resume_arg

    # --resume 无 ID：自动找最近的未完成会话
    resumable = find_resumable_sessions()
    if not resumable:
        console.print("[warning]⚠ 没有未完成的工作流会话[/warning]")
        return None

    # 按时间降序排列，取最近的
    latest = resumable[-1]
    step_label = STEP_LABELS.get(latest.current_step, latest.current_step.value)
    desc = latest.problem_description[:60] + "..." if len(latest.problem_description) > 60 else latest.problem_description
    console.print(
        f"[info]自动恢复最近的会话: {latest.session_id}[/info]\n"
        f"  [dim]步骤: {step_label} | 问题: {desc}[/dim]"
    )
    return latest.session_id


def handle(args: argparse.Namespace) -> None:
    """run 子命令回调"""
    from rich.console import Console

    console = get_console()
    print_header()

    verbose = getattr(args, "verbose", False)
    user_log_files = getattr(args, "log_files", None) or []

    try:
        # 解析 --resume：显式 ID / 自动最近 / 未指定
        resume_id = _resolve_resume_id(args.resume, console)

        if resume_id:
            # 恢复模式
            engine = WorkflowEngine.resume(
                resume_id,
                auto=args.auto,
                verbose=verbose,
                user_log_files=user_log_files,
            )

        else:
            # 新建模式：先检查是否有未完成会话
            engine = WorkflowEngine.find_and_prompt_resume(
                auto=args.auto,
                verbose=verbose,
            )

            if engine is None:
                # 用户选择新建或无未完成会话
                description = args.description
                if not description:
                    description = interact.prompt_input(
                        "请描述部署问题",
                        validator=lambda v: None if v.strip() else "问题描述不能为空",
                    )

                console.print(
                    f"[info]问题: {description}[/info]"
                )
                engine = WorkflowEngine.start_new(
                    description,
                    auto=args.auto,
                    verbose=verbose,
                    user_log_files=user_log_files,
                )
                console.print(f"[info]会话已创建: {engine.state.session_id}[/info]")
                console.print(
                    f"[dim]模式: {'自动' if args.auto else '逐步'} | "
                    f"可随时 Ctrl+C 中断，使用 --resume 恢复[/dim]\n"
                )

        # 运行工作流
        engine.run()

    except WorkflowError as e:
        console.print(f"\n[danger]✗ {e.message}[/danger]")
        if e.hint:
            console.print(f"  💡 {e.hint}")
