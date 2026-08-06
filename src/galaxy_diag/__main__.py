"""Galaxy 诊断工具 CLI 入口

启动流程：加载配置 → 硬件预检 → 模型健康检查 → 系统就绪

对应 REQ-A-01：模型离线部署与运行
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from galaxy_diag.config.settings import load_config
from galaxy_diag.shared.errors import ConfigError
from galaxy_diag.model.health import HealthChecker
from galaxy_diag.model.precheck import HardwarePrechecker

console = Console()

# 项目根目录（src/galaxy_diag/__main__.py → 上两级为项目根，含 config.yaml）
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_CONFIG = _PROJECT_ROOT / "config.yaml"


def main():
    """CLI 入口"""
    console.print("[bold cyan]Galaxy-Diag[/bold cyan] — 银河平台部署问题定位工具\n")

    # 1. 加载配置（优先项目根目录的 config.yaml，可用 GALAXY_CONFIG 覆盖）
    console.print("[bold]📂 加载配置...[/bold]")
    config_path = os.environ.get("GALAXY_CONFIG", str(_DEFAULT_CONFIG))
    try:
        config = load_config(config_path)
    except ConfigError as e:
        console.print(f"[red]✗ 配置加载失败[/red]")
        console.print(f"  {e}")
        sys.exit(1)

    console.print(
        f"  推理服务: {config.llm.base_url}\n"
        f"  模型: {config.llm.model}\n"
    )

    # 2. 硬件资源预检
    console.print("[bold]🔍 硬件资源预检[/bold]")
    prechecker = HardwarePrechecker(config.hardware)
    precheck_result = prechecker.check()
    _print_precheck_table(precheck_result)

    if not precheck_result.passed:
        console.print(f"\n[red]{precheck_result.summary}[/red]")
        sys.exit(1)

    console.print("[green]  硬件预检通过[/green]\n")

    # 3. 推理服务健康检查
    console.print("[bold]🔍 推理服务健康检查[/bold]")
    checker = HealthChecker(config.llm)
    health = checker.check()
    _print_health_result(health)

    if not health.ok:
        console.print(f"\n[red]  {health.message}[/red]")
        if health.hint:
            console.print(f"  💡 {health.hint}")
        sys.exit(1)

    console.print(f"[green]  {health.message}[/green]\n")

    # 4. 系统就绪
    console.print(
        f"[bold green]✅ 系统就绪[/bold green]  "
        f"模型: {config.llm.model}  |  "
        f"服务: {config.llm.base_url}"
    )

    # 5. 进入 CLI 交互（后续 REQ-F 实现，当前占位）
    # TODO: REQ-F-02 诊断-修复端到端工作流


def _print_precheck_table(result):
    """用 Rich 表格输出预检结果"""
    table = Table(show_header=True, header_style="bold")
    table.add_column("项目", width=12)
    table.add_column("最低要求", width=10)
    table.add_column("实际", width=10)
    table.add_column("状态", width=4)

    for item in result.items:
        status = "✅" if item.passed else "❌"
        actual_str = f"{item.actual:.1f}" if item.actual != int(item.actual) else str(int(item.actual))
        table.add_row(
            item.name,
            f"{item.required} {item.unit}",
            f"{actual_str} {item.unit}",
            status,
        )

    console.print(table)

    # 输出附注（如 GPU 的 note）
    for item in result.items:
        if item.note:
            console.print(f"  [dim]{item.name}: {item.note}[/dim]")


def _print_health_result(health):
    """输出健康检查结果"""
    if health.ok:
        console.print(f"  服务地址: {health.message}")
    else:
        console.print(f"  [red]{health.message}[/red]")
        if health.available_models is not None and health.available_models:
            console.print(
                f"  可用模型: {', '.join(health.available_models)}"
            )


if __name__ == "__main__":
    main()
