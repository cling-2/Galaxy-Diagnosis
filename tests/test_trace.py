"""Trace 模块单元测试 (REQ-X-04)

验证 TraceRecorder 记录、JSONL 持久化、TraceViewer 重建、field_update 合并、
崩溃安全（未关闭 Span）、CLI 命令注册。
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from galaxy_diag.trace import viewer
from galaxy_diag.trace.recorder import (
    TraceRecorder,
    get_recorder,
    reset_recorder,
    set_recorder,
)


@pytest.fixture
def trace_dir(tmp_path: Path) -> Path:
    return tmp_path / "traces"


def test_recorder_writes_trace_open_and_close(trace_dir: Path) -> None:
    """recorder 创建时写 trace_open，close_trace 时写 trace_close"""
    r = TraceRecorder("sess-1", "问题描述", trace_dir=trace_dir)
    r.close_trace("done")

    lines = (trace_dir / "sess-1.jsonl").read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["record_type"] == "trace_open"
    assert first["session_id"] == "sess-1"
    assert first["problem_description"] == "问题描述"
    second = json.loads(lines[1])
    assert second["record_type"] == "trace_close"
    assert second["final_status"] == "done"


def test_span_open_close_records_duration(trace_dir: Path) -> None:
    """span 上下文管理器写 span_open + span_close，含 status 和 duration_ms"""
    r = TraceRecorder("sess-2", "问题", trace_dir=trace_dir)
    with r.span("diagnosing", 1):
        pass
    r.close_trace("done")

    records = _read_jsonl(trace_dir / "sess-2.jsonl")
    span_opens = [r for r in records if r["record_type"] == "span_open"]
    span_closes = [r for r in records if r["record_type"] == "span_close"]
    assert len(span_opens) == 1
    assert span_opens[0]["span_id"] == "diagnosing_1"
    assert span_opens[0]["step"] == "diagnosing"
    assert span_opens[0]["sequence_index"] == 1
    assert len(span_closes) == 1
    assert span_closes[0]["status"] == "completed"
    assert "duration_ms" in span_closes[0]


def test_skipped_span_only_open_no_close(trace_dir: Path) -> None:
    """skipped Span 只写 span_open（status=skipped），不写 span_close"""
    r = TraceRecorder("sess-3", "问题", trace_dir=trace_dir)
    with r.span("collecting", 1, skip_reason="prematch_confirmed"):
        pass
    r.close_trace("done")

    records = _read_jsonl(trace_dir / "sess-3.jsonl")
    span_opens = [r for r in records if r["record_type"] == "span_open"]
    span_closes = [r for r in records if r["record_type"] == "span_close"]
    assert len(span_opens) == 1
    assert span_opens[0]["status"] == "skipped"
    assert span_opens[0]["skip_reason"] == "prematch_confirmed"
    assert len(span_closes) == 0  # skipped 不写 close


def test_event_attached_to_current_span(trace_dir: Path) -> None:
    """event 自动归属栈顶 span，event_id 递增"""
    r = TraceRecorder("sess-4", "问题", trace_dir=trace_dir)
    with r.span("diagnosing", 1):
        r.record_event("RuleMatch", result="CONFIRMED", rules_count=3)
        r.record_event("LLMCall", model="m", completion="c", status="success")
    r.close_trace("done")

    tree = viewer.load_trace("sess-4", trace_dir=trace_dir)
    assert tree is not None
    span = tree.spans[0]
    assert span.span_id == "diagnosing_1"
    assert len(span.events) == 2
    assert span.events[0].event_type == "RuleMatch"
    assert span.events[0].event_id == "diagnosing_1_1"
    assert span.events[0].data["result"] == "CONFIRMED"
    assert span.events[1].event_id == "diagnosing_1_2"


def test_field_update_merges_to_event(trace_dir: Path) -> None:
    """update_last_events 写 field_update 行，viewer 加载时合并到对应 event"""
    r = TraceRecorder("sess-5", "问题", trace_dir=trace_dir)
    with r.span("diagnosing", 1):
        r.record_event("LLMCall", model="m", completion="c", parse_ok=None, status="success")
        r.update_last_events("LLMCall", parse_ok=True, parsed_result={"root_cause": "x"})
    r.close_trace("done")

    tree = viewer.load_trace("sess-5", trace_dir=trace_dir)
    assert tree is not None
    llm_events = [e for s in tree.spans for e in s.events if e.event_type == "LLMCall"]
    assert len(llm_events) == 1
    assert llm_events[0].data["parse_ok"] is True
    assert llm_events[0].data["parsed_result"] == {"root_cause": "x"}


def test_no_recorder_context_is_none() -> None:
    """无 recorder 时 get_recorder 返回 None（测试/未启用场景）"""
    assert get_recorder() is None


def test_crash_safety_unclosed_span_marked_interrupted(trace_dir: Path) -> None:
    """崩溃场景：未关闭的 Span 在 viewer 加载时标记 interrupted"""
    r = TraceRecorder("sess-6", "问题", trace_dir=trace_dir)
    # 写一个 span_open 但不 close（模拟崩溃）
    r._write_line({
        "record_type": "span_open",
        "span_id": "diagnosing_1",
        "step": "diagnosing",
        "sequence_index": 1,
    })
    # 不写 span_close，直接 close_trace
    r.close_trace("interrupted")

    tree = viewer.load_trace("sess-6", trace_dir=trace_dir)
    assert tree is not None
    assert tree.spans[0].status == "interrupted"
    assert tree.final_status == "interrupted"


def test_resume_appends_to_same_file(trace_dir: Path) -> None:
    """resume 场景：追加到同一文件，不重写"""
    # 第一次运行
    r1 = TraceRecorder("sess-7", "问题", trace_dir=trace_dir)
    with r1.span("env_recognising", 1):
        r1.record_event("RuleMatch", result="NONE", rules_count=5)
    r1.close_trace("interrupted")

    first_count = len(_read_jsonl(trace_dir / "sess-7.jsonl"))

    # 第二次（模拟 resume）：新建 recorder 追加
    r2 = TraceRecorder("sess-7", "问题", trace_dir=trace_dir)
    with r2.span("collecting", 1):
        r2.record_event("ToolCall", tool_name="collect_system_resources", status="success")
    r2.close_trace("done")

    lines = (trace_dir / "sess-7.jsonl").read_text(encoding="utf-8").strip().split("\n")
    # 追加后行数应大于第一次
    assert len(lines) > first_count
    # 文件开头仍是第一次的 trace_open
    first = json.loads(lines[0])
    assert first["record_type"] == "trace_open"


def test_viewer_handles_corrupt_line(trace_dir: Path) -> None:
    """viewer 跳过损坏的 JSON 行"""
    path = trace_dir / "sess-8.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"record_type": "trace_open", "session_id": "sess-8"}\n'
        'CORRUPT LINE {{{\n'
        '{"record_type": "trace_close", "final_status": "done"}\n',
        encoding="utf-8",
    )
    tree = viewer.load_trace("sess-8", trace_dir=trace_dir)
    assert tree is not None
    assert tree.final_status == "done"


def test_viewer_returns_none_for_missing_file(trace_dir: Path) -> None:
    """文件不存在时 viewer 返回 None"""
    tree = viewer.load_trace("nonexistent", trace_dir=trace_dir)
    assert tree is None


def test_cli_command_registered() -> None:
    """trace 子命令已注册到 _COMMANDS"""
    from galaxy_diag.workflow.cli.app import _COMMANDS

    names = [name for _, name in _COMMANDS]
    assert "trace" in names


def test_cli_trace_command_not_in_precheck_required() -> None:
    """trace 命令不需要硬件预检（不调用 LLM）"""
    from galaxy_diag.workflow.cli.app import _PRECHECK_REQUIRED_COMMANDS

    assert "trace" not in _PRECHECK_REQUIRED_COMMANDS


def test_render_does_not_raise_on_all_event_types(tmp_path: Path) -> None:
    """render 在所有 Event 类型 + unknown status + skipped span 下不抛 Rich MarkupError"""
    import io
    from rich.console import Console

    from galaxy_diag.trace.recorder import TraceRecorder

    trace_dir = tmp_path / "traces"
    r = TraceRecorder("sess-render", "问题", trace_dir=trace_dir)
    # skipped span (轻量)
    r._write_line({"record_type": "span_open", "span_id": "env_recognising_1",
                   "step": "env_recognising", "sequence_index": 1,
                   "status": "skipped", "skip_reason": "prematch"})
    with r.span("diagnosing", 1):
        r.record_event("RuleMatch", rules_count=5, result="SUSPECTED", matched_rule_id="r1",
                       matched_keywords=[], rule_hint="h", diagnosis_source="LLM", status="success")
        r.record_event("RAGRetrieval", query_text="q",
                       matches=[{"case_id": "c1", "similarity": 0.9, "summary": "s", "env_type": None}],
                       top_k=3, min_similarity=0.0, best_similarity=0.9, status="success")
        r.record_event("LLMCall", model="m", completion="output", truncated=False,
                       prompt_summary=[{"role": "system", "content_length": 100,
                                        "contains": ["user_input"], "template_hash": "ab"}],
                       parse_ok=True, parsed_result={"root_cause": "x"},
                       usage={"prompt_tokens": 10, "completion_tokens": 5}, status="success")
    with r.span("reviewing", 1):
        r.record_event("SecurityCheck", check_type="execution_guard", guard_level="critical",
                       matched_patterns=["rm -rf"], impact_summary="高危", message="msg", status="success")
        r.record_event("HITL", type="review_reject", decision="rejected", guard_level="critical",
                       edited_fields=None, impact="流程终止")
    # unknown status span (open 无 close) → viewer 标记 interrupted
    r._write_line({"record_type": "span_open", "span_id": "executing_1",
                   "step": "executing", "sequence_index": 1})
    r.close_trace("rejected")

    tree = viewer.load_trace("sess-render", trace_dir=trace_dir)
    assert tree is not None

    # 默认渲染 + verbose 渲染 + step 过滤均不应抛异常
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=True, width=100, no_color=True)
    viewer.render(tree, console=console)  # 不抛 = 通过
    viewer.render(tree, console=console, verbose=True)  # 不抛 = 通过
    viewer.render(tree, console=console, step_filter="DIAGNOSING")  # 不抛 = 通过


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").strip().split("\n") if line.strip()]
