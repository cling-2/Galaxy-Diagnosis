"""galaxy-diag diagnose — 独立诊断分析

对应需求: REQ-C-01 / REQ-C-02 / REQ-C-03

独立执行诊断分析（不经过完整工作流）：
  环境感知 → 信息采集 → 规则匹配/LLM 推理 → 输出结论

适用于快速诊断场景（不需要修复建议时）。
"""

from __future__ import annotations

import argparse

# 注意：重型导入（collector/diagnoser/model）放在 handle() 内部，
# 避免拖慢其他子命令的启动速度（app.py 在启动时导入所有子命令模块）。


def register(subparsers: argparse._SubParsersAction) -> None:
    sub = subparsers.add_parser(
        "diagnose",
        help="独立诊断分析（环境感知 + 信息采集 + 根因推理）",
        description="快速诊断：自动识别环境、采集诊断信息、推理根因，输出带置信度的诊断结论。",
    )
    sub.add_argument(
        "-d", "--description",
        required=True,
        help="问题描述（如 '容器网络不通'、'磁盘挂载失败'）",
    )
    sub.add_argument(
        "--log-file",
        action="append",
        default=[],
        help="用户上传的日志文件路径（可多次指定）",
    )
    sub.add_argument(
        "--no-collect",
        action="store_true",
        help="跳过信息采集，仅做 LLM 推理（需配合 --session 使用已采集的上下文）",
    )
    sub.add_argument(
        "--output",
        choices=["table", "json"],
        default="table",
        help="输出格式（默认 table）",
    )


def handle(args: argparse.Namespace) -> None:
    from galaxy_diag.collector import collect_env
    from galaxy_diag.config.settings import load_config
    from galaxy_diag.diagnoser import build_diagnostic_context, diagnose
    from galaxy_diag.model.client import ModelAdapter
    from galaxy_diag.shared.errors import GalaxyDiagError
    from galaxy_diag.shared.types import DiagnosisSource
    from galaxy_diag.workflow.cli.display import (
        get_console,
        print_diagnosis,
        print_diagnostic_context,
        print_env_info,
    )

    console = get_console()

    try:
        # 1. 环境感知
        console.print("[info]识别运行环境...[/info]")
        env_info = collect_env()
        print_env_info(env_info)

        # 2. 信息采集
        console.print("\n[info]采集诊断信息...[/info]")
        ctx = build_diagnostic_context(
            problem_description=args.description,
            env_info=env_info,
            user_log_files=args.log_file or None,
        )
        print_diagnostic_context(ctx)

        # 3. 根因分析
        console.print("\n[info]分析故障根因...[/info]")
        console.print(
            "[dim]  LLM 推理中，纯 CPU 模式下可能需要 3-5 分钟，请耐心等待...[/dim]"
        )
        config = load_config()
        model_adapter = ModelAdapter(config.llm)

        result = diagnose(
            problem_description=args.description,
            env_info=env_info,
            diagnostic_context=ctx,
            model_adapter=model_adapter,
        )

        # 4. 来源提示（异常处理：明确告知用户故障原因）
        if result.diagnosis_source == DiagnosisSource.ERROR_FALLBACK:
            console.print(
                "[error]⚠ LLM 推理服务不可用，已降级为信息不足结论[/error]"
            )
        elif result.diagnosis_source == DiagnosisSource.LLM_FALLBACK:
            console.print(
                "[warning]⚠ LLM 推理结果校验部分失败，已自动修复[/warning]"
            )

        # 5. 输出结论
        console.print()
        print_diagnosis(result)

        # 6. JSON 输出模式
        if args.output == "json":
            import json
            from dataclasses import asdict

            console.print("\n[dim]--- JSON ---[/dim]")
            console.print_json(json.dumps(asdict(result), ensure_ascii=False, default=str))

    except GalaxyDiagError as e:
        console.print(f"\n[danger]✗ {e.message}[/danger]")
        if e.hint:
            console.print(f"  💡 {e.hint}")
