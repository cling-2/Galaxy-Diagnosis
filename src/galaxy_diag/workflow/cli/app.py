"""CLI 主入口 & 命令注册

解析命令行参数，分发子命令，初始化全局 Console。
不包含任何业务逻辑。
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys

# 抑制 Ollama 推理服务低级别日志输出到终端
# 这些日志（如 "slot Ursine operator" "disabling mmap"）来自 Ollama 服务端进程，
# 与工具输出混在一起会干扰用户阅读。
# 注意：此环境变量仅对新启动的 Ollama 进程生效；对已运行的服务需重启。
if not os.environ.get("OLLAMA_LOG_LEVEL"):
    os.environ["OLLAMA_LOG_LEVEL"] = "ERROR"

from galaxy_diag.config.settings import load_config
from galaxy_diag.model.precheck import HardwarePrechecker
from galaxy_diag.workflow.cli.display import get_console, init_console, print_header, print_precheck_result

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
        "--skip-precheck",
        action="store_true",
        help="跳过硬件资源预检 (调试/CI 用，不推荐在生产环境使用)",
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


def _run_precheck(*, skip: bool, config_path: str | None) -> None:
    """启动前硬件资源预检 (REQ-A-01 验收标准 6)

    检测 CPU 核数、内存、磁盘、GPU 显存是否满足最低要求。
    不满足时打印差距提示并拒绝启动 (sys.exit(1))。
    预检只读 /proc、shutil、nvidia-smi，零网络、零 LLM，符合离线约束。

    Args:
        skip: True 时跳过预检（--skip-precheck，调试/CI 用）
        config_path: 配置文件路径（用于读取 hardware 最低要求配置）
    """
    if skip:
        return

    console = get_console()

    try:
        config = load_config(config_path)
        result = HardwarePrechecker(config.hardware).check()
        print_precheck_result(result)
        if not result.passed:
            console.print("\n[danger]✗ 硬件资源不满足最低要求，拒绝启动。[/danger]")
            console.print("[dim]  使用 --skip-precheck 可跳过预检（不推荐）[/dim]")
            console.print("[dim]  请升级硬件后重试，参考最低配置见 deployment.md[/dim]")
            sys.exit(1)
    except SystemExit:
        raise
    except Exception as e:
        # 预检本身异常不应阻断启动（对齐任务书"错误处理不吞"——打印但不致命阻断，
        # 让用户能在预检故障时继续使用工具）
        console.print(f"[warning]⚠ 硬件预检异常，已跳过: {e}[/warning]")


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

    # 启动前硬件资源预检（子命令分发前执行；仅对实际运行的子命令触发，
    # --help 等不走此分支）
    _run_precheck(skip=args.skip_precheck, config_path=args.config)

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
