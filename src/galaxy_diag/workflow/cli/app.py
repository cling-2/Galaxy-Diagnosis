"""CLI 主入口 & 命令注册

解析命令行参数，分发子命令，初始化全局 Console。
不包含任何业务逻辑。
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys

# 抑制本地推理服务（Ollama / llama.cpp llama-server）的低级别日志输出到终端。
# Ollama 日志如 "slot ..." "disabling mmap"；llama-server 日志如
# "srv server_strea:" "slot print_timing:" "[GIN] ... | 200 |"，来自服务端进程，
# 与工具输出混在一起会干扰用户阅读。
#
# 设置的环境变量（仅在用户未显式设置时填充默认值）：
#   OLLAMA_LOG_LEVEL=ERROR  → Ollama 服务端日志级别
#   LLAMA_LOG_LEVEL=3       → llama.cpp 日志级别（0=DEBUG 1=INFO 2=WARN 3=ERROR，新版）
#   GIN_MODE=release        → llama-server 的 gin HTTP 框架访问日志（关闭 [GIN] 行）
#
# 约束：galaxy-diag 不启动推理服务（仅 HTTP API 连接），这些变量只对【设置后启动、
# 且继承本进程环境的服务进程】生效。对已在运行的服务需重启并导出这些变量，
# 或以 `2>logfile` 重定向 stderr（见 deploy/install_offline.sh）。
def _apply_log_suppression_env() -> None:
    """防御性设置推理服务日志抑制环境变量（不覆盖用户显式设置）。"""
    if not os.environ.get("OLLAMA_LOG_LEVEL"):
        os.environ["OLLAMA_LOG_LEVEL"] = "ERROR"
    if not os.environ.get("LLAMA_LOG_LEVEL"):
        os.environ["LLAMA_LOG_LEVEL"] = "3"
    if not os.environ.get("GIN_MODE"):
        os.environ["GIN_MODE"] = "release"


_apply_log_suppression_env()

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
    ("galaxy_diag.workflow.cli.cmd_kb", "kb"),
]


# 需要 LLM 推理（因而依赖硬件资源）的子命令集合。
# 仅这些命令在分发前触发硬件预检；env/snapshot/audit-log/completion/fix/review
# 不调用模型，跳过预检。新增需要预检的命令时在此追加命令名即可。
_PRECHECK_REQUIRED_COMMANDS: frozenset[str] = frozenset({"run", "diagnose"})

# kb 子命令中需要 embedding（因而需预检）的子动作
_KB_PRECHECK_ACTIONS: frozenset[str] = frozenset({"import", "reindex"})


def _needs_precheck(args: argparse.Namespace) -> bool:
    """是否需要执行硬件预检

    仅当子命令在 _PRECHECK_REQUIRED_COMMANDS 中、且非 --mock 模式时返回 True。
    --mock 模式使用预设响应、不连接真实 LLM，无需硬件资源预检。
    kb import/reindex 需要 embedding 模型，也触发预检；kb list/delete 不需要。
    """
    if args.command not in _PRECHECK_REQUIRED_COMMANDS:
        # kb import/reindex 需 embedding，需预检；kb list/delete 不需要
        if args.command == "kb":
            return getattr(args, "kb_action", None) in _KB_PRECHECK_ACTIONS
        return False
    # mock 属性仅 run 子命令定义；其余命令 getattr 回退为 False
    if getattr(args, "mock", False):
        return False
    return True


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
        result = HardwarePrechecker(
            config.hardware,
            embed_model=config.llm.embed_model,
            base_url=config.llm.base_url,
        ).check()
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

    # 启动前硬件预检：仅对调用 LLM 的子命令（run / diagnose）执行；
    # --mock 模式同样跳过。env/snapshot/audit-log/completion/fix/review 不触发 LLM，无需预检。
    if _needs_precheck(args):
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
