"""CLI 主入口 & 命令注册

解析命令行参数，分发子命令，初始化全局 Console。
不包含任何业务逻辑。
"""

from __future__ import annotations

import argparse
import importlib
import sys

from galaxy_diag.workflow.cli.display import get_console, init_console, print_header

# ===== 子命令注册表 =====

# 模块路径 → 命令名
_COMMANDS: list[tuple[str, str]] = [
    ("galaxy_diag.workflow.cli.cmd_env", "env"),
    ("galaxy_diag.workflow.cli.cmd_diagnose", "diagnose"),
    ("galaxy_diag.workflow.cli.cmd_fix", "fix"),
    ("galaxy_diag.workflow.cli.cmd_review", "review"),
    ("galaxy_diag.workflow.cli.cmd_snapshot", "snapshot"),
    ("galaxy_diag.workflow.cli.cmd_audit_log", "audit-log"),
    ("galaxy_diag.workflow.cli.cmd_run", "run"),
    ("galaxy_diag.workflow.cli.cmd_completion", "completion"),
]


def _register_commands(subparsers: argparse._SubParsersAction) -> None:
    """动态注册所有子命令"""
    for module_path, name in _COMMANDS:
        mod = importlib.import_module(module_path)
        mod.register(subparsers)


def _build_parser() -> argparse.ArgumentParser:
    """构建顶层 ArgumentParser"""
    parser = argparse.ArgumentParser(
        prog="galaxy-diag",
        description="银河平台部署问题定位工具 — 离线诊断修复命令行工具",
        epilog="使用 galaxy-diag <子命令> --help 查看子命令详细用法",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="配置文件路径 (默认: config.yaml)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="详细输出模式 (等效 log_level=DEBUG)",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="禁用颜色输出 (等同 NO_COLOR=1)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 0.1.0",
    )

    subparsers = parser.add_subparsers(
        dest="command",
        title="子命令",
        description="可用子命令:",
    )
    _register_commands(subparsers)

    return parser


def main() -> None:
    """CLI 入口函数"""
    parser = _build_parser()

    # argcomplete 补全（可选依赖，不可用时静默跳过）
    try:
        import argcomplete
        argcomplete.autocomplete(parser)
    except ImportError:
        pass

    args = parser.parse_args()

    # 初始化全局 Console（--no-color / NO_COLOR 环境变量）
    init_console(no_color=args.no_color)
    console = get_console()

    # 无子命令时打印 help
    if args.command is None:
        print_header()
        parser.print_help()
        console.print("")  # 尾部空行
        return

    # 分发到子命令回调
    try:
        args.callback(args)
    except KeyboardInterrupt:
        console.print("\n[dim]已中断[/dim]")
        sys.exit(130)
    except Exception as e:
        if args.verbose:
            console.print_exception()
        else:
            console.print(f"[danger]✗ 内部错误: {e}[/danger]")
            console.print("[dim]  使用 --verbose 查看完整堆栈[/dim]")
        sys.exit(1)
