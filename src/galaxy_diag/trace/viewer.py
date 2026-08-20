"""Trace 查询与展示 (REQ-X-04)

从 JSONL 文件加载 trace，重建 Trace→Span→Event 树，Rich 树形渲染。
field_update 行按 target_event_id 合并到对应 Event。

对齐 docs/Trace_design.md §存储格式 §CLI 命令。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.tree import Tree

from galaxy_diag.workflow.cli.display import get_console

# 默认 trace 目录
_TRACE_DIR = Path.home() / ".galaxy-diag" / "traces"


# ===== 数据类 =====


@dataclass
class TraceEvent:
    """一条 Event 记录"""
    event_id: str
    event_type: str
    span_id: str
    timestamp: str
    data: dict[str, Any] = field(default_factory=dict)

    # 常用字段快捷访问
    @property
    def status(self) -> str:
        return self.data.get("status", "")

    @property
    def duration_ms(self) -> int | None:
        v = self.data.get("duration_ms")
        return v if isinstance(v, int) else None


@dataclass
class TraceSpan:
    """一个 Step Span"""
    span_id: str
    step: str
    sequence_index: int
    status: str = "unknown"  # completed / failed / skipped / interrupted
    skip_reason: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    duration_ms: int | None = None
    event_count: int = 0
    events: list[TraceEvent] = field(default_factory=list)

    @property
    def is_skipped(self) -> bool:
        return self.status == "skipped"


@dataclass
class TraceTree:
    """一次诊断的完整 Trace"""
    session_id: str
    start_time: str | None = None
    end_time: str | None = None
    problem_description: str = ""
    final_status: str = ""
    span_count: int = 0
    spans: list[TraceSpan] = field(default_factory=list)


# ===== 加载 =====


def load_trace(
    session_id: str,
    trace_dir: Path | None = None,
) -> TraceTree | None:
    """从 JSONL 文件加载 trace，重建树结构

    Args:
        session_id: 会话 ID
        trace_dir: trace 目录（默认 ~/.galaxy-diag/traces/）

    Returns:
        TraceTree 或 None（文件不存在）
    """
    trace_dir = trace_dir or _TRACE_DIR
    path = trace_dir / f"{session_id}.jsonl"

    if not path.exists():
        # 尝试备用路径
        fallback_dir = Path.home() / ".galaxy-diag" / "traces.failed"
        fallback_path = fallback_dir / f"{session_id}.jsonl"
        if fallback_path.exists():
            path = fallback_path
        else:
            return None

    # 第一遍：读取所有行
    records: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # 跳过损坏行
    except OSError:
        return None

    if not records:
        return None

    # 第二遍：收集 field_update，按 target_event_id 索引
    field_updates: dict[str, dict[str, Any]] = {}
    for rec in records:
        if rec.get("record_type") == "field_update":
            target_id = rec.get("target_event_id", "")
            if target_id:
                # 合并多次 update
                if target_id not in field_updates:
                    field_updates[target_id] = {}
                for k, v in rec.items():
                    if k not in ("record_type", "target_event_id"):
                        field_updates[target_id][k] = v

    # 第三遍：建树
    tree = TraceTree(session_id=session_id)
    spans_by_id: dict[str, TraceSpan] = {}
    open_spans: set[str] = set()  # 追踪已 open 但未 close 的 span

    for rec in records:
        rtype = rec.get("record_type", "")

        if rtype == "trace_open":
            tree.start_time = rec.get("start_time")
            tree.problem_description = rec.get("problem_description", "")

        elif rtype == "trace_close":
            tree.end_time = rec.get("end_time")
            tree.final_status = rec.get("final_status", "")
            tree.span_count = rec.get("span_count", 0)

        elif rtype == "span_open":
            span_id = rec.get("span_id", "")
            span = TraceSpan(
                span_id=span_id,
                step=rec.get("step", ""),
                sequence_index=rec.get("sequence_index", 0),
                status=rec.get("status", "unknown"),
                skip_reason=rec.get("skip_reason"),
            )
            spans_by_id[span_id] = span
            open_spans.add(span_id)
            tree.spans.append(span)

        elif rtype == "span_close":
            span_id = rec.get("span_id", "")
            if span_id in spans_by_id:
                span = spans_by_id[span_id]
                span.end_time = rec.get("end_time")
                span.status = rec.get("status", "completed")
                span.event_count = rec.get("event_count", 0)
                span.duration_ms = rec.get("duration_ms")
                open_spans.discard(span_id)

        elif rtype == "event":
            span_id = rec.get("span_id", "")
            event_id = rec.get("event_id", "")

            # 合并 field_update
            event_data = {k: v for k, v in rec.items()
                          if k not in ("record_type", "span_id", "event_id", "event_type", "timestamp")}
            if event_id in field_updates:
                event_data.update(field_updates[event_id])

            event = TraceEvent(
                event_id=event_id,
                event_type=rec.get("event_type", ""),
                span_id=span_id,
                timestamp=rec.get("timestamp", ""),
                data=event_data,
            )
            if span_id in spans_by_id:
                spans_by_id[span_id].events.append(event)
            # else: orphan event（span 未 open 就记录了），暂忽略

        # field_update 已在第二遍处理，此处跳过

    # 未关闭的 Span 标记 interrupted
    for span_id in open_spans:
        if span_id in spans_by_id:
            spans_by_id[span_id].status = "interrupted"

    return tree


# ===== 渲染 =====

# 步骤中文标签
_STEP_LABELS = {
    "env_recognising": "环境识别",
    "collecting": "信息采集",
    "diagnosing": "根因分析",
    "planning": "修复建议生成",
    "security_checking": "安全检测",
    "execution_guard": "执行前熔断",
    "reviewing": "人工审核",
    "snapshot": "快照创建",
    "executing": "执行修复",
    "verifying": "结果验证",
}

# Event 类型中文标签
_EVENT_LABELS = {
    "ToolCall": "🔧 Tool 调用",
    "LLMCall": "🧠 LLM 推理",
    "RuleMatch": "📋 规则匹配",
    "RAGRetrieval": "📚 RAG 检索",
    "HITL": "👤 人工介入",
    "SecurityCheck": "🛡️ 安全检查",
}

# 状态样式
_STATUS_STYLES = {
    "completed": "success",
    "failed": "danger",
    "skipped": "dim",
    "interrupted": "warning",
}


def _styled(text: str, style: str) -> str:
    """安全应用 Rich 样式，空样式不加标签"""
    if style:
        return f"[{style}]{text}[/{style}]"
    return text


def render(
    tree: TraceTree,
    *,
    step_filter: str | None = None,
    verbose: bool = False,
    console: Console | None = None,
) -> None:
    """Rich 树形渲染 Trace

    Args:
        tree: 已加载的 Trace 树
        step_filter: 按步骤名过滤（如 "DIAGNOSING"）
        verbose: 显示完整 completion / output_summary
        console: Rich Console（默认使用 display.get_console()）
    """
    if console is None:
        console = get_console()

    # 根节点
    root_label = f"[heading]Trace: {tree.session_id}[/heading]"
    if tree.final_status:
        style = _STATUS_STYLES.get(tree.final_status, "")
        root_label += "  " + _styled(tree.final_status, style)
    root_tree = Tree(root_label)

    # 问题描述
    if tree.problem_description:
        desc = tree.problem_description
        if len(desc) > 100:
            desc = desc[:100] + "..."
        root_tree.add(f"[dim]问题: {desc}[/dim]")

    # 时间信息
    if tree.start_time:
        root_tree.add(f"[dim]开始: {tree.start_time}[/dim]")
    if tree.end_time:
        root_tree.add(f"[dim]结束: {tree.end_time}[/dim]")

    # Span 列表
    for span in tree.spans:
        # 步骤过滤
        if step_filter and span.step.upper() != step_filter.upper():
            continue

        _render_span(root_tree, span, verbose)

    console.print(root_tree)


def _render_span(parent: Tree, span: TraceSpan, verbose: bool) -> None:
    """渲染单个 Span"""
    step_label = _STEP_LABELS.get(span.step, span.step)
    status_style = _STATUS_STYLES.get(span.status, "")

    # Span 标题
    label = _styled(step_label, status_style)
    if span.sequence_index > 1:
        label += f" [dim](第 {span.sequence_index} 次)[/dim]"
    if span.is_skipped:
        label += f" [dim]跳过: {span.skip_reason or '未指定原因'}[/dim]"
    elif span.status != "completed":
        label += " " + _styled(span.status, status_style)

    # 耗时
    if span.duration_ms is not None:
        if span.duration_ms >= 1000:
            label += f" [dim]{span.duration_ms / 1000:.1f}s[/dim]"
        else:
            label += f" [dim]{span.duration_ms}ms[/dim]"

    span_node = parent.add(label)

    # Event 列表
    for event in span.events:
        _render_event(span_node, event, verbose)


def _render_event(parent: Tree, event: TraceEvent, verbose: bool) -> None:
    """渲染单个 Event"""
    event_label = _EVENT_LABELS.get(event.event_type, event.event_type)
    data = event.data

    # 耗时
    duration = data.get("duration_ms")
    duration_str = ""
    if isinstance(duration, int):
        if duration >= 1000:
            duration_str = f" [dim]{duration / 1000:.1f}s[/dim]"
        else:
            duration_str = f" [dim]{duration}ms[/dim]"

    # 根据 event_type 定制渲染
    if event.event_type == "ToolCall":
        tool_name = data.get("tool_name", "?")
        output_status = data.get("output_status", "")
        status_icon = "✓" if output_status == "success" else "⚠"
        label = f"{event_label} {tool_name} {status_icon}{duration_str}"
        node = parent.add(label)
        if verbose:
            output_summary = data.get("output_summary", "")
            if output_summary:
                _add_truncated_text(node, "摘要", output_summary, max_lines=10)
        # input_params
        input_params = data.get("input_params")
        if input_params and verbose:
            _add_dict_summary(node, "参数", input_params, max_items=5)

    elif event.event_type == "LLMCall":
        model = data.get("model", "?")
        parse_ok = data.get("parse_ok")
        parse_icon = ""
        if parse_ok is True:
            parse_icon = " ✓"
        elif parse_ok is False:
            parse_icon = " ✗解析失败"
        completion = data.get("completion", "")
        truncated = data.get("truncated", False)
        trunc_tag = " [dim][截断][/dim]" if truncated else ""

        label = f"{event_label} {model}{parse_icon}{duration_str}{trunc_tag}"
        node = parent.add(label)

        # prompt_summary
        prompt_summary = data.get("prompt_summary", [])
        if prompt_summary:
            ps_parts = []
            for ps in prompt_summary:
                role = ps.get("role", "?")
                length = ps.get("content_length", 0)
                contains = ps.get("contains", [])
                contains_str = f" [{', '.join(contains)}]" if contains else ""
                ps_parts.append(f"{role}({length}字符{contains_str})")
            node.add("[dim]Prompt: " + " → ".join(ps_parts) + "[/dim]")

        # completion（verbose 或短文本时显示）
        if completion and (verbose or len(completion) <= 200):
            _add_truncated_text(node, "LLM 输出", completion, max_lines=8 if verbose else 3)

        # parsed_result
        parsed = data.get("parsed_result")
        if parsed:
            _add_dict_summary(node, "解析结果", parsed, max_items=8)

        # usage
        usage = data.get("usage", {})
        if usage:
            pt = usage.get("prompt_tokens", 0)
            ct = usage.get("completion_tokens", 0)
            node.add(f"[dim]Token: prompt={pt}, completion={ct}[/dim]")

    elif event.event_type == "RuleMatch":
        result = data.get("result", "?")
        matched_rule = data.get("matched_rule_id")
        rules_count = data.get("rules_count", "?")

        # 决策高亮
        result_style = "success" if result == "CONFIRMED" else ("warning" if result == "SUSPECTED" else "dim")
        label = f"{event_label} [{result_style}]{result}[/{result_style}]"
        if matched_rule:
            label += f" [dim]{matched_rule}[/dim]"
        label += f" [dim](评估 {rules_count} 条规则)[/dim]"
        label += duration_str
        parent.add(label)

    elif event.event_type == "RAGRetrieval":
        matches = data.get("matches", [])
        best_sim = data.get("best_similarity", 0)
        query = data.get("query_text", "")

        label = f"{event_label} {len(matches)} 条匹配"
        if matches:
            label += f" [dim](最佳相似度 {best_sim:.2f})[/dim]"
        label += duration_str
        node = parent.add(label)

        if verbose and query:
            _add_truncated_text(node, "查询", query, max_lines=3)
        for m in matches[:3]:  # 最多显示 3 条
            case_id = m.get("case_id", "?")
            sim = m.get("similarity", 0)
            summary = m.get("summary", "")
            sim_style = "success" if sim >= 0.8 else ("warning" if sim >= 0.5 else "dim")
            match_label = f"[{sim_style}]相似度 {sim:.2f}[/{sim_style}] [dim]{case_id}[/dim]"
            m_node = node.add(match_label)
            if summary and verbose:
                m_node.add(f"[dim]{summary[:100]}[/dim]")

    elif event.event_type == "HITL":
        hitl_type = data.get("type", "?")
        decision = data.get("decision", "?")
        guard_level = data.get("guard_level")
        impact = data.get("impact", "")

        # 决策高亮
        dec_style = "success" if decision == "confirmed" else ("danger" if decision == "rejected" else "info")
        label = f"{event_label} [{dec_style}]{decision}[/{dec_style}]"
        if hitl_type:
            label += f" [dim]({hitl_type})[/dim]"
        if guard_level and guard_level != "pass":
            gl_style = "danger" if guard_level == "critical" else "warning"
            label += f" [{gl_style}]熔断: {guard_level}[/{gl_style}]"
        label += duration_str
        node = parent.add(label)
        if impact:
            node.add(f"[dim]影响: {impact}[/dim]")
        edited = data.get("edited_fields")
        if edited:
            node.add(f"[dim]编辑字段: {', '.join(str(f) for f in edited)}[/dim]")

    elif event.event_type == "SecurityCheck":
        check_type = data.get("check_type", "?")
        guard_level = data.get("guard_level", "?")
        matched = data.get("matched_patterns", [])

        gl_style = "success" if guard_level == "pass" else ("danger" if guard_level == "critical" else "warning")
        label = f"{event_label} [{gl_style}]{guard_level}[/{gl_style}]"
        label += f" [dim]({check_type})[/dim]"
        label += duration_str
        node = parent.add(label)
        if matched:
            for pat in matched[:5]:
                node.add(f"[dim]- {pat}[/dim]")
        impact = data.get("impact_summary")
        if impact:
            node.add(f"[dim]影响: {impact}[/dim]")

    else:
        # 未知 Event 类型
        label = f"{event_label}{duration_str}"
        node = parent.add(label)
        if verbose:
            _add_dict_summary(node, "数据", data, max_items=10)


def _add_truncated_text(parent: Tree, label: str, text: str, max_lines: int = 5) -> None:
    """向树节点添加截断的文本"""
    lines = text.strip().split("\n")
    if len(lines) > max_lines:
        shown = "\n".join(lines[:max_lines])
        node = parent.add(f"[dim]{label}:[/dim]")
        node.add(shown)
        node.add(f"[dim]... 省略 {len(lines) - max_lines} 行[/dim]")
    else:
        node = parent.add(f"[dim]{label}:[/dim]")
        node.add(text.strip())


def _add_dict_summary(parent: Tree, label: str, data: Any, max_items: int = 5) -> None:
    """向树节点添加 dict/list 摘要"""
    if isinstance(data, dict):
        items = list(data.items())[:max_items]
        parts = [f"{k}={_short_repr(v)}" for k, v in items]
        if len(data) > max_items:
            parts.append(f"... (+{len(data) - max_items})")
        parent.add(f"[dim]{label}: {{{', '.join(parts)}}}[/dim]")
    elif isinstance(data, list):
        items = [_short_repr(v) for v in data[:max_items]]
        if len(data) > max_items:
            items.append(f"... (+{len(data) - max_items})")
        parent.add(f"[dim]{label}: [{', '.join(items)}][/dim]")
    else:
        parent.add(f"[dim]{label}: {_short_repr(data)}[/dim]")


def _short_repr(obj: Any, max_len: int = 50) -> str:
    """短 repr，截断长字符串"""
    try:
        s = repr(obj)
    except Exception:
        s = str(obj)
    if len(s) > max_len:
        return s[:max_len] + "..."
    return s
