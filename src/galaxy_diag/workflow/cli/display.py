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

from galaxy_diag.shared.constants import (
    CHECK_CATEGORY_LABELS,
    CHECK_SEVERITY_LABELS,
    CONFIDENCE_LABELS,
    ENV_TYPE_LABELS,
    FIX_SOURCE_LABELS,
)

if TYPE_CHECKING:
    from galaxy_diag.model.precheck import PrecheckResult
    from galaxy_diag.shared.types import (
        AuditRecord,
        DiagnosisResult,
        DiagnosticContext,
        EnvInfo,
        FixProposal,
        SnapshotMeta,
        VerifyResult,
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


def print_env_info(env_info: EnvInfo, *, format: str = "table", skip_hardware: bool = False) -> None:
    """渲染环境识别结果

    Args:
        env_info: 环境采集结果
        format: 输出格式 "table" | "json" | "yaml"
        skip_hardware: 是否跳过了完整硬件/存储采集（C类精简采集）。
            True 时不打印硬件信息表和存储信息表，仅显示跳过提示。
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

    # C类：跳过完整硬件采集时，不打印空表，仅显示跳过提示
    if skip_hardware:
        console.print("[dim]📋 硬件信息: 已跳过完整硬件和存储采集（问题类型不需要）[/dim]")
    else:
        _render_hardware_table(env_info)
        _render_storage_table(env_info)

    # 采集提示
    if env_info.collection_warnings:
        console.print("\n[warning]⚠ 采集提示[/warning]")
        for w in env_info.collection_warnings:
            console.print(f"  - {w}")


def _render_hardware_table(env_info: EnvInfo) -> None:
    """渲染硬件信息表"""
    console = get_console()
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


def _render_storage_table(env_info: EnvInfo) -> None:
    """渲染存储信息表"""
    console = get_console()
    if env_info.storage:
        console.print("\n[heading]💾 存储信息[/heading]")
        st_table = Table(show_header=True, header_style="bold", pad_edge=False)
        st_table.add_column("类型", width=8)
        st_table.add_column("挂载路径", min_width=15)
        st_table.add_column("文件系统", width=10)

        for st in env_info.storage:
            st_table.add_row(st.storage_type, st.mount_path, st.filesystem)

        console.print(st_table)


def print_diagnostic_context(ctx: DiagnosticContext) -> None:
    """渲染诊断信息采集结果

    Args:
        ctx: 诊断上下文
    """
    console = get_console()
    from galaxy_diag.shared.constants import CONTAINER_RUNTIME_LABELS

    # 环境信息
    env_label = ENV_TYPE_LABELS.get(ctx.env_info_ref, str(ctx.env_info_ref))
    runtime_label = ""
    if ctx.container_runtime:
        runtime_label = f" ({CONTAINER_RUNTIME_LABELS.get(ctx.container_runtime.value, ctx.container_runtime.value)})"

    console.print(f"[heading]🔍 诊断信息采集结果[/heading]")
    console.print(f"  环境: [info]{env_label}{runtime_label}[/info]\n")

    # 组件状态
    if ctx.component_status:
        console.print("[heading]📦 组件状态[/heading]")
        table = Table(show_header=True, header_style="bold", pad_edge=False)
        table.add_column("组件", min_width=18)
        table.add_column("状态", width=10)
        table.add_column("详情", min_width=20)

        status_styles = {
            "running": "success",
            "failed": "danger",
            "inactive": "warning",
            "unknown": "dim",
        }
        for comp in ctx.component_status:
            style = status_styles.get(comp.get("status", ""), "dim")
            table.add_row(
                comp.get("name", ""),
                f"[{style}]{comp.get('status', 'unknown')}[/{style}]",
                comp.get("detail", ""),
            )
        console.print(table)
        console.print("")

    # 日志片段
    if ctx.log_snippets:
        console.print(f"[heading]📄 日志片段 ({len(ctx.log_snippets)} 条)[/heading]")
        for snip in ctx.log_snippets:
            level_style = {"ERROR": "danger", "Warning": "warning"}.get(snip.level, "dim")
            console.print(f"  [{level_style}][{snip.level}][/{level_style}] {snip.source}")
            if snip.truncated:
                console.print(f"    [dim](已截断)[/dim]")
        console.print("")

    # 系统资源
    if ctx.system_resources:
        console.print("[heading]💻 系统资源[/heading]")
        for key, value in ctx.system_resources.items():
            # 多行值（如 disk_usage 是 df 的多行输出）：逐行原样输出保持列对齐，
            # 不加 key 前缀（df 自身有列头，前缀会破坏列间空格对齐）
            if isinstance(value, str) and "\n" in value:
                console.print(f"  {key}:")
                for line in value.splitlines():
                    console.print(f"    {line}")
            else:
                console.print(f"  {key}: {value}")
        console.print("")

    # 网络连通性
    if ctx.network_checks:
        console.print("[heading]🌐 网络连通性[/heading]")
        for check in ctx.network_checks:
            reachable = check.get("reachable", False)
            style = "success" if reachable else "danger"
            mark = "✓" if reachable else "✗"
            console.print(f"  [{style}]{mark}[/{style}] {check.get('target', '')}")
        console.print("")

    # 用户上传
    if ctx.user_provided:
        console.print(f"[heading]📎 用户日志 ({len(ctx.user_provided)} 个)[/heading]")

    # 采集提示
    if ctx.collection_warnings:
        console.print("[warning]⚠ 采集提示[/warning]")
        for w in ctx.collection_warnings:
            console.print(f"  - {w}")

    # 采集的工具（可追溯）
    if ctx.collected_tools:
        console.print(f"[dim]  已调用工具: {', '.join(ctx.collected_tools)}[/dim]")


def print_diagnosis(result: DiagnosisResult) -> None:
    """渲染诊断结论

    Args:
        result: 诊断结果
    """
    console = get_console()
    conf_label = CONFIDENCE_LABELS.get(result.confidence.value, str(result.confidence))

    # 来源标签
    from galaxy_diag.shared.constants import DIAGNOSIS_SOURCE_LABELS
    from galaxy_diag.shared.types import DiagnosisSource

    source_label = DIAGNOSIS_SOURCE_LABELS.get(
        result.diagnosis_source.value, ""
    )

    # 置信度样式映射
    conf_styles = {
        "confirmed": "success",
        "suspected": "warning",
        "insufficient": "danger",
    }
    conf_style = conf_styles.get(result.confidence.value, "warning")

    # 标题：含来源标签
    title_parts = [f"诊断结论 ({conf_label})"]
    if source_label:
        title_parts.append(source_label)
    title_text = f"[{conf_style}]{' - '.join(title_parts)}[/{conf_style}]"

    # ERROR_FALLBACK 时加警告前缀
    if result.diagnosis_source == DiagnosisSource.ERROR_FALLBACK:
        console.print(
            "[error]⚠ 推理服务不可用，根因分析未完成[/error]"
        )

    console.print(Panel(
        Markdown(result.root_cause or "未得出结论"),
        title=title_text,
        border_style=conf_style,
        padding=(1, 2),
    ))

    # 证据
    if result.evidence:
        console.print("\n[heading]📎 支撑证据[/heading]")
        for i, ev in enumerate(result.evidence, 1):
            console.print(f"  {i}. {ev}")

    # 故障范围
    if result.fault_scope:
        console.print(f"\n[info]影响范围: {result.fault_scope}[/info]")

    # 排查步骤
    if result.investigation_steps:
        console.print("\n[info]排查步骤:[/info]")
        for i, step in enumerate(result.investigation_steps, 1):
            console.print(f"  [dim]{i}.[/dim] {step}")

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

    # 来源标签
    source_label = FIX_SOURCE_LABELS.get(proposal.source.value, "")
    if source_label:
        console.print(f"[dim]来源: {source_label}[/dim]")

    # 命令步骤表格（仅显示修复步骤，验证步骤在步骤 7/7 结果验证时执行）
    fix_commands = [cmd for cmd in proposal.commands if not cmd.is_verification]
    verify_commands = [cmd for cmd in proposal.commands if cmd.is_verification]
    if fix_commands:
        console.print("[heading]🔧 修复步骤[/heading]")
        # 风险列用 ratio=2 让 Rich 分配更多宽度，不再硬截断
        # （REQ-D-01 验收标准 7：风险提示需完整可见）
        table = Table(show_header=True, header_style="bold", pad_edge=False)
        table.add_column("#", width=3)
        table.add_column("命令", min_width=25)
        table.add_column("说明", min_width=10)
        table.add_column("风险", min_width=16, ratio=2, overflow="fold")
        table.add_column("执行位置", width=8)

        for i, cmd in enumerate(fix_commands, 1):
            risk_style = "danger" if cmd.risk_note else "dim"
            risk_text = f"[{risk_style}]{cmd.risk_note or '无'}[/{risk_style}]"
            loc = "[warning]宿主机[/warning]" if cmd.requires_host else "本机"
            table.add_row(str(i), cmd.command, cmd.description, risk_text, loc)

        console.print(table)

    # 验证步骤提示
    if verify_commands:
        console.print(
            f"\n[dim]📋 验证步骤（{len(verify_commands)} 条）将在步骤 7/7 结果验证时执行:[/dim]"
        )
        for cmd in verify_commands:
            console.print(f"  [dim]- {cmd.command}  ({cmd.description})[/dim]")

    # 脚本
    if proposal.script:
        console.print(f"\n[heading]📜 修复脚本 ({proposal.script_language or 'bash'})[/heading]")
        console.print(Panel(proposal.script, border_style="dim", padding=(1, 2)))

    # 结构化检测结果（check_detail 优先于 check_issues）
    if proposal.check_detail is not None:
        result = proposal.check_detail
        if result.issues:
            status_style = "danger" if result.has_critical else "warning"
            console.print(f"\n[{status_style}]🔍 多维检测结果[/{status_style}]")
            for issue in result.issues:
                sev_label = CHECK_SEVERITY_LABELS.get(issue.severity.value, issue.severity.value)
                cat_label = CHECK_CATEGORY_LABELS.get(issue.category, issue.category)
                sev_style = "danger" if issue.severity.value == "critical" else "warning"
                console.print(
                    f"  [{sev_style}][{sev_label}][{cat_label}][/{sev_style}] {issue.message}"
                )
                if issue.suggestion:
                    console.print(f"    [dim]💡 {issue.suggestion}[/dim]")
    elif proposal.check_issues:
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


def print_verify_result(result: VerifyResult) -> None:
    """渲染验证结果

    Args:
        result: 验证结果（safety/verifier.py verify() 产出）
    """
    console = get_console()

    if result.total_steps == 0:
        console.print("[warning]⚠ 未执行验证（修复建议无验证步骤），建议人工确认修复效果[/warning]")
        return

    table = Table(show_header=True, header_style="bold", pad_edge=False)
    table.add_column("指标", width=12)
    table.add_column("值", min_width=20)

    status_text = "[success]通过[/success]" if result.success else "[danger]失败[/danger]"
    table.add_row("结果", status_text)
    table.add_row("通过步骤", f"{result.passed_steps}/{result.total_steps}")
    if not result.success:
        table.add_row("失败步骤", str(result.failed_step))
        table.add_row("失败说明", result.failed_description)

    console.print(Panel(table, title="[heading]🔬 验证结果[/heading]", border_style="info", padding=(1, 2)))


def print_next_steps(proposal: FixProposal, snapshot_id: str | None) -> None:
    """渲染验证失败后的进一步排查建议 + 一键回滚提示

    Args:
        proposal: 修复建议（用于提示补充验证命令信息）
        snapshot_id: 快照 ID（用于一键回滚命令），无快照时为 None
    """
    console = get_console()

    lines: list[str] = []
    lines.append("[dim]本次修复未解决问题，建议按以下顺序排查：[/dim]\n")
    lines.append("  [info]1.[/info] 检查验证命令的输出日志，确认具体失败原因")
    if snapshot_id:
        lines.append(
            f"  [info]2.[/info] 使用 [warning]galaxy-diag snapshot rollback {snapshot_id}[/warning] 一键回滚到修复前状态"
        )
        lines.append("  [info]3.[/info] 回滚后，补充以下信息重新运行诊断：")
        rollback_note = "回滚后重新运行"
    else:
        lines.append("  [info]2.[/info] 补充以下信息重新运行诊断（无可用快照，需人工确认当前系统状态）：")
        lines.append("  [info]3.[/info] 重新运行诊断：")
        rollback_note = "重新运行"

    lines.append("      - 验证命令的完整输出和错误信息")
    lines.append("      - 修复执行期间系统日志: [dim]journalctl --since \"5 minutes ago\"[/dim]")
    lines.append("      - 受影响服务的当前状态")
    lines.append(f"  [info]4.[/info] 重新运行: [info]galaxy-diag run -d \"补充描述\"[/info]")

    console.print(Panel(
        "\n".join(lines),
        title="[heading]🔧 进一步排查建议[/heading]",
        border_style="warning",
        padding=(1, 2),
    ))

    # 一键回滚提示
    if snapshot_id:
        console.print(
            "\n[warning]⚠ 回滚提示:[/warning]\n"
            f"  执行 [warning]galaxy-diag snapshot rollback {snapshot_id}[/warning]\n"
            f"  可恢复到修复前的系统状态"
        )
    else:
        console.print(
            "\n[warning]⚠ 本次修复未创建快照，无法一键回滚，请人工评估系统状态后决定下一步[/warning]"
        )


def print_precheck_result(result: PrecheckResult) -> None:
    """渲染硬件资源预检结果 (REQ-A-01 验收标准 6)

    使用 Rich Table 对齐 deployment.md 预期输出格式。

    Args:
        result: 预检汇总（HardwarePrechecker.check() 产出）
    """
    console = get_console()

    console.print("[heading]🔍 硬件资源预检[/heading]")

    table = Table(show_header=True, header_style="bold", pad_edge=False)
    table.add_column("项目", width=10)
    table.add_column("最低要求", width=12)
    table.add_column("实际", width=12)
    table.add_column("状态", width=4)

    for item in result.items:
        status = "[success]✓[/success]" if item.passed else "[danger]✗[/danger]"
        required_str = f"{item.required} {item.unit}"
        # 实际值格式化：CPU 用整数，其他保留 1 位小数
        if item.unit == "核":
            actual_str = f"{int(item.actual)} {item.unit}"
        else:
            actual_str = f"{item.actual:.1f} {item.unit}"
        table.add_row(item.name, required_str, actual_str, status)

    console.print(table)

    # 打印各项目的额外说明（如 GPU 未检测到的提示）
    for item in result.items:
        if item.note:
            style = "warning" if not item.passed else "dim"
            console.print(f"  [{style}]{item.name}: {item.note}[/{style}]")

    # 汇总行
    if result.passed:
        console.print("\n[success]  ✓ 硬件预检通过[/success]")
    else:
        console.print(f"\n[danger]  ✗ {result.summary.split(chr(10))[0]}[/danger]")
