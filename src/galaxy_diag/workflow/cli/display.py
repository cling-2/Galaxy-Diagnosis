"""Rich 输出：样式常量、全局 Console、领域渲染组件

所有终端输出通过此模块统一管理，确保样式一致。
禁止在业务代码中直接写颜色值（如 [green]），必须使用语义样式名。
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.theme import Theme

from galaxy_diag.shared.constants import CONFIDENCE_LABELS, ENV_TYPE_LABELS

if TYPE_CHECKING:
    from galaxy_diag.shared.types import (
        AuditRecord,
        DiagnosisResult,
        EnvInfo,
        FixProposal,
        SnapshotMeta,
    )

# ===== 样式常量 =====

STYLE_SUCCESS = "green"
STYLE_DANGER = "red bold"
STYLE_WARNING = "yellow"
STYLE_INFO = "cyan"
STYLE_DIM = "dim"
STYLE_HEADING = "bold cyan"

# Rich Theme 注册（确保样式集中定义）
GALAXY_THEME = Theme({
    "success": STYLE_SUCCESS,
    "danger": STYLE_DANGER,
    "warning": STYLE_WARNING,
    "info": STYLE_INFO,
    "heading": STYLE_HEADING,
})

# ===== 全局 Console =====

_console: Console | None = None


def init_console(*, no_color: bool = False) -> None:
    """初始化全局 Console，支持 --no-color 和 NO_COLOR 环境变量"""
    global _console
    _console = Console(theme=GALAXY_THEME, no_color=no_color)


def get_console() -> Console:
    """获取全局 Console 实例"""
    global _console
    if _console is None:
        # 检查 NO_COLOR 环境变量（https://no-color.org/）
        no_color = os.environ.get("NO_COLOR") is not None
        init_console(no_color=no_color)
    return _console


# ===== 工具函数 =====


def print_header() -> None:
    """打印工具标题头"""
    console = get_console()
    console.print("[heading]Galaxy-Diag[/heading] — 银河平台部署问题定位工具\n")


def print_stub_notice(req_id: str, description: str) -> None:
    """打印模块尚未实现的提示

    Args:
        req_id: 需求编号，如 "REQ-B"
        description: 模块描述，如 "环境识别与硬件采集"
    """
    console = get_console()
    console.print(
        f"[warning]⚠ {description}模块尚未实现 ({req_id})[/warning]\n"
        f"[dim]  CLI 框架已就绪，业务模块将在后续迭代中填充[/dim]"
    )


# ===== 领域渲染组件 =====


def print_env_info(env_info: EnvInfo, *, format: str = "table") -> None:
    """渲染环境识别结果

    Args:
        env_info: 环境采集结果
        format: 输出格式 "table" | "json" | "yaml"
    """
    console = get_console()
    env_label = ENV_TYPE_LABELS.get(env_info.env_type, str(env_info.env_type))

    if format == "json":
        import json
        from dataclasses import asdict
        console.print_json(json.dumps(asdict(env_info), ensure_ascii=False, indent=2))
        return

    console.print(f"[heading]🔍 环境识别结果[/heading]")
    console.print(f"  环境类型: [info]{env_label}[/info]\n")

    # 硬件信息表格
    hw = env_info.hardware
    console.print("[heading]📋 硬件信息[/heading]")
    table = Table(show_header=True, header_style="bold", pad_edge=False)
    table.add_column("项目", width=12)
    table.add_column("值", min_width=20)

    table.add_row("CPU", hw.cpu_model or "未检测到")
    table.add_row("核数", str(hw.cpu_cores) if hw.cpu_cores else "未检测到")
    table.add_row("内存", f"{hw.memory_total_gb:.1f} GB" if hw.memory_total_gb else "未检测到")

    # 磁盘
    if hw.disks:
        disk_strs = [f"{d.model or d.type or 'unknown'} {d.capacity}" for d in hw.disks]
        table.add_row("磁盘", "\n".join(disk_strs))
    else:
        table.add_row("磁盘", "未检测到")

    # RAID 卡
    if hw.raid_cards:
        raid_strs = [f"{r.model or 'unknown'} (固件: {r.firmware_version or 'unknown'})" for r in hw.raid_cards]
        table.add_row("RAID 卡", "\n".join(raid_strs))
    else:
        table.add_row("RAID 卡", "未检测到")

    # 网卡
    if hw.nics:
        nic_strs = [f"{n.model or 'unknown'} ({n.driver or 'unknown'})" for n in hw.nics]
        table.add_row("网卡", "\n".join(nic_strs))
    else:
        table.add_row("网卡", "未检测到")

    console.print(table)

    # 存储信息
    if env_info.storage:
        console.print("\n[heading]💾 存储信息[/heading]")
        st_table = Table(show_header=True, header_style="bold", pad_edge=False)
        st_table.add_column("类型", width=8)
        st_table.add_column("挂载路径", min_width=15)
        st_table.add_column("文件系统", width=10)

        for st in env_info.storage:
            st_table.add_row(st.storage_type, st.mount_path, st.filesystem)

        console.print(st_table)

    # 采集提示
    if env_info.collection_warnings:
        console.print("\n[warning]⚠ 采集提示[/warning]")
        for w in env_info.collection_warnings:
            console.print(f"  - {w}")


def print_diagnosis(result: DiagnosisResult) -> None:
    """渲染诊断结论

    Args:
        result: 诊断结果
    """
    console = get_console()
    conf_label = CONFIDENCE_LABELS.get(result.confidence.value, str(result.confidence))

    # 置信度样式映射
    conf_styles = {
        "confirmed": "success",
        "suspected": "warning",
        "insufficient": "danger",
    }
    conf_style = conf_styles.get(result.confidence.value, "warning")

    console.print(Panel(
        Markdown(result.root_cause or "未得出结论"),
        title=f"[{conf_style}]诊断结论 ({conf_label})[/{conf_style}]",
        border_style=conf_style,
        padding=(1, 2),
    ))

    # 证据
    if result.evidence:
        console.print("\n[heading]📎 支撑证据[/heading]")
        for i, ev in enumerate(result.evidence, 1):
            console.print(f"  {i}. {ev}")

    # 缺失信息
    if result.missing_info:
        console.print("\n[warning]⚠ 信息不足，以下信息有助于进一步定位:[/warning]")
        for item in result.missing_info:
            console.print(f"  [dim]- {item}[/dim]")


def print_fix_proposal(proposal: FixProposal) -> None:
    """渲染修复建议

    Args:
        proposal: 修复建议
    """
    console = get_console()

    # 命令步骤表格
    if proposal.commands:
        console.print("[heading]🔧 修复步骤[/heading]")
        table = Table(show_header=True, header_style="bold", pad_edge=False)
        table.add_column("#", width=3)
        table.add_column("命令", min_width=30)
        table.add_column("说明", min_width=15)
        table.add_column("风险", width=12)

        for i, cmd in enumerate(proposal.commands, 1):
            risk_style = "danger" if cmd.risk_note else "dim"
            risk_text = f"[{risk_style}]{cmd.risk_note or '无'}[/{risk_style}]"
            table.add_row(str(i), cmd.command, cmd.description, risk_text)

        console.print(table)

    # 脚本
    if proposal.script:
        console.print(f"\n[heading]📜 修复脚本 ({proposal.script_language or 'bash'})[/heading]")
        console.print(Panel(proposal.script, border_style="dim", padding=(1, 2)))

    # 检测结果
    if proposal.check_issues:
        console.print(f"\n[{'danger' if not proposal.check_passed else 'warning'}]🔍 多维检测结果[/{'danger' if not proposal.check_passed else 'warning'}]")
        for issue in proposal.check_issues:
            console.print(f"  [danger]✗ {issue}[/danger]")

    # 整体风险
    if proposal.risk_notes:
        console.print("\n[warning]⚠ 风险提示[/warning]")
        for note in proposal.risk_notes:
            console.print(f"  - {note}")

    # 影响范围
    if proposal.impact_scope:
        console.print(f"\n[info]📊 影响范围: {proposal.impact_scope}[/info]")


def print_audit_records(records: list[AuditRecord]) -> None:
    """渲染审计日志

    Args:
        records: 审计记录列表
    """
    console = get_console()

    if not records:
        console.print("[dim]暂无审计记录[/dim]")
        return

    from galaxy_diag.shared.constants import AUDIT_RESULT_LABELS

    table = Table(show_header=True, header_style="bold", pad_edge=False)
    table.add_column("时间", width=19)
    table.add_column("会话", width=10)
    table.add_column("操作", min_width=20)
    table.add_column("结果", width=8)
    table.add_column("确认输入", width=12)

    for rec in records:
        ts = rec.timestamp.strftime("%Y-%m-%d %H:%M:%S") if rec.timestamp else "-"
        result_label = AUDIT_RESULT_LABELS.get(rec.result, rec.result)
        result_style = {
            "成功": "success", "失败": "danger",
            "已回滚": "warning", "已拒绝": "dim",
        }.get(result_label, "dim")
        table.add_row(
            ts,
            rec.session_id[:8] if rec.session_id else "-",
            rec.action,
            f"[{result_style}]{result_label}[/{result_style}]",
            rec.user_input or "-",
        )

    console.print(table)


def print_snapshot_meta(meta: SnapshotMeta) -> None:
    """渲染快照元数据

    Args:
        meta: 快照元数据
    """
    console = get_console()
    ts = meta.timestamp.strftime("%Y-%m-%d %H:%M:%S") if meta.timestamp else "-"

    console.print(Panel(
        f"[bold]快照 ID:[/bold] {meta.snapshot_id}\n"
        f"[bold]时间:[/bold]     {ts}\n"
        f"[bold]操作摘要:[/bold] {meta.operation_summary}\n"
        f"[bold]备份路径:[/bold] {meta.backup_path}",
        title="[heading]📸 快照详情[/heading]",
        border_style="info",
        padding=(1, 2),
    ))

    if meta.affected_files:
        console.print("[dim]  受影响文件:[/dim]")
        for f in meta.affected_files:
            console.print(f"[dim]    - {f}[/dim]")

    if meta.affected_services:
        console.print("[dim]  受影响服务:[/dim]")
        for s in meta.affected_services:
            console.print(f"[dim]    - {s}[/dim]")


def print_snapshot_list(metas: list[SnapshotMeta]) -> None:
    """渲染快照列表

    Args:
        metas: 快照元数据列表
    """
    console = get_console()

    if not metas:
        console.print("[dim]暂无快照[/dim]")
        return

    table = Table(show_header=True, header_style="bold", pad_edge=False)
    table.add_column("快照 ID", width=24)
    table.add_column("时间", width=19)
    table.add_column("操作摘要", min_width=20)

    for meta in metas:
        ts = meta.timestamp.strftime("%Y-%m-%d %H:%M:%S") if meta.timestamp else "-"
        table.add_row(meta.snapshot_id, ts, meta.operation_summary)

    console.print(table)
