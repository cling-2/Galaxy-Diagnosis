"""Trace 记录器 (REQ-X-04)

TraceRecorder 通过 JSONL 追加写入记录推理链路。
通过 contextvars 隐式传递，无需穿透业务函数签名。

JSONL 行类型：
- trace_open / trace_close：Trace 级生命周期
- span_open / span_close：Step Span 级
- event：细粒度事件（ToolCall / LLMCall / RuleMatch / RAGRetrieval / HITL / SecurityCheck）
- field_update：补充已写入 Event 的字段（JSONL 不可原地修改，用补充行合并）

存储路径：~/.galaxy-diag/traces/<session_id>.jsonl

对齐 docs/Trace_design.md §决策 4/5。
"""

from __future__ import annotations

import json
import sys
import time
from collections import deque
from contextvars import ContextVar
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Generator

# ContextVar 隐式传递 recorder
_trace_recorder: ContextVar[TraceRecorder | None] = ContextVar(
    "trace_recorder", default=None
)


def get_recorder() -> TraceRecorder | None:
    """获取当前上下文的 TraceRecorder（隐式传递入口）"""
    return _trace_recorder.get()


def set_recorder(recorder: TraceRecorder) -> Any:
    """设置 recorder 到 contextvar，返回 token 供 reset"""
    return _trace_recorder.set(recorder)


def reset_recorder(token: Any) -> None:
    """重置 contextvar（配合 set_recorder 返回的 token）"""
    _trace_recorder.reset(token)


# 默认 trace 目录
_TRACE_DIR = Path.home() / ".galaxy-diag" / "traces"

# 备用目录
_TRACE_DIR_FALLBACK = Path.home() / ".galaxy-diag" / "traces.failed"

# completion 截断上限 (8KB)
_MAX_COMPLETION_BYTES = 8192

# output_summary 初始截断上限 (2KB)
_MAX_OUTPUT_SUMMARY_INIT_BYTES = 2048

# _recent_events deque 上限
_RECENT_EVENTS_MAX = 10


class TraceRecorder:
    """推理链路记录器

    通过 JSONL 追加写入，崩溃安全（已写入行全有效）。
    Span 用上下文管理器声明，Event 自动归属栈顶 Span。

    用法：
        recorder = TraceRecorder(session_id, problem_description)
        token = set_recorder(recorder)
        try:
            with recorder.span("DIAGNOSING", 1):
                recorder.record_event("RuleMatch", result="CONFIRMED", ...)
        finally:
            recorder.close_trace("done")
            reset_recorder(token)
    """

    def __init__(
        self,
        session_id: str,
        problem_description: str,
        trace_dir: Path | None = None,
    ):
        self._session_id = session_id
        self._trace_dir = trace_dir or _TRACE_DIR
        self._file = None
        self._span_stack: list[str] = []  # 当前 Span 栈（栈顶 = 当前 span_id）
        self._event_counter: dict[str, int] = {}  # span_id → event 序号
        self._span_counter: int = 0  # 已打开的 Span 总数（用于 span_count）
        self._recent_events: deque[tuple[str, dict]] = deque(
            maxlen=_RECENT_EVENTS_MAX
        )  # (event_id, record) 供 update_last_events 定位
        self._closed = False

        # 创建目录并打开文件
        try:
            self._trace_dir.mkdir(parents=True, exist_ok=True)
            path = self._trace_dir / f"{session_id}.jsonl"
            self._file = open(path, "a", encoding="utf-8")
        except OSError:
            # 备用路径
            try:
                _TRACE_DIR_FALLBACK.mkdir(parents=True, exist_ok=True)
                path = _TRACE_DIR_FALLBACK / f"{session_id}.jsonl"
                self._file = open(path, "a", encoding="utf-8")
            except OSError:
                import sys
                print(
                    f"[trace 告警] trace 文件创建失败: session_id={session_id}",
                    file=sys.stderr,
                )

        # 写 trace_open
        self._write_line({
            "record_type": "trace_open",
            "session_id": session_id,
            "start_time": datetime.now().isoformat(),
            "problem_description": problem_description,
        })

    # ===== Trace 生命周期 =====

    def close_trace(self, final_status: str, span_count: int | None = None) -> None:
        """写 trace_close，关闭文件"""
        if self._closed:
            return
        self._closed = True
        self._write_line({
            "record_type": "trace_close",
            "end_time": datetime.now().isoformat(),
            "final_status": final_status,
            "span_count": span_count if span_count is not None else self._span_counter,
        })
        if self._file:
            try:
                self._file.close()
            except OSError:
                pass

    # ===== Span 上下文管理器 =====

    @contextmanager
    def span(
        self,
        step: str,
        seq: int,
        *,
        skip_reason: str | None = None,
    ) -> Generator[None, None, None]:
        """Step Span 上下文管理器

        Args:
            step: WorkflowStep 枚举名
            seq: 同一 Step 的执行序号（从 1 开始）
            skip_reason: 非 None 时表示 skipped Span（只写 span_open，不进栈，不写 span_close）
        """
        span_id = f"{step}_{seq}"
        start_time = time.monotonic()
        self._span_counter += 1

        # span_open
        open_record: dict[str, Any] = {
            "record_type": "span_open",
            "span_id": span_id,
            "step": step,
            "sequence_index": seq,
        }
        if skip_reason is not None:
            open_record["status"] = "skipped"
            open_record["skip_reason"] = skip_reason
        self._write_line(open_record)

        if skip_reason is not None:
            # Skipped Span：不进栈，不写 span_close
            yield
            return

        # 正常 Span：进栈
        self._span_stack.append(span_id)
        self._event_counter[span_id] = 0

        exc_info = None
        try:
            yield
        except BaseException as exc:
            exc_info = exc
            raise
        finally:
            # 弹栈
            if self._span_stack and self._span_stack[-1] == span_id:
                self._span_stack.pop()

            # span_close
            end_time = time.monotonic()
            if exc_info is not None:
                status = "interrupted"
            else:
                status = "completed"
            event_count = self._event_counter.pop(span_id, 0)
            self._write_line({
                "record_type": "span_close",
                "span_id": span_id,
                "end_time": datetime.now().isoformat(),
                "status": status,
                "event_count": event_count,
                "duration_ms": int((end_time - start_time) * 1000),
            })

    # ===== Event 记录 =====

    def record_event(self, event_type: str, **kwargs: Any) -> None:
        """记录一个 Event

        自动填充 span_id（栈顶）、event_id、timestamp。
        duration_ms 需要调用方传入（入口拦截时测量）。

        Args:
            event_type: ToolCall / LLMCall / RuleMatch / RAGRetrieval / HITL / SecurityCheck
            **kwargs: Event 字段（由各 Event 类型定义决定）
        """
        span_id = self._span_stack[-1] if self._span_stack else "orphan"
        event_seq = self._event_counter.get(span_id, 0) + 1
        self._event_counter[span_id] = event_seq
        event_id = f"{span_id}_{event_seq}"

        record: dict[str, Any] = {
            "record_type": "event",
            "span_id": span_id,
            "event_id": event_id,
            "event_type": event_type,
            "timestamp": datetime.now().isoformat(),
            **kwargs,
        }
        self._write_line(record)

        # 维护 _recent_events 供 update_last_events 定位
        self._recent_events.append((event_id, record))

    def update_last_events(self, event_type: str, **fields: Any) -> None:
        """补充最近一条该类型 Event 的字段

        JSONL 不可原地修改已写入行，因此写一条 field_update 补充行。
        viewer.load_trace() 时按 target_event_id 合并。

        Args:
            event_type: 要补充的 Event 类型（如 "LLMCall"）
            **fields: 待合并字段（如 parsed_result=..., parse_ok=True）
        """
        # 在 _recent_events 中倒序查找
        target_event_id = None
        for event_id, record in reversed(self._recent_events):
            if record.get("event_type") == event_type:
                target_event_id = event_id
                break

        if target_event_id is None:
            return  # 没找到，静默跳过

        self._write_line({
            "record_type": "field_update",
            "target_event_id": target_event_id,
            **fields,
        })

    # ===== 内部方法 =====

    def _write_line(self, record: dict[str, Any]) -> None:
        """写一行 JSONL（json.dumps + append + flush）"""
        if not self._file:
            return
        try:
            line = json.dumps(record, ensure_ascii=False, default=_json_default)
            self._file.write(line + "\n")
            self._file.flush()
        except OSError:
            # 写入失败：打印告警但不抛异常（trace 不阻塞核心操作）
            print(
                f"[trace 告警] trace 写入失败",
                file=sys.stderr,
            )


def _json_default(obj: Any) -> Any:
    """json.dumps 的 default 处理：支持 datetime / Enum"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    # Enum 支持
    if hasattr(obj, "value"):
        return obj.value
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


# ===== prompt_summary 推导工具函数 =====


def build_prompt_summary(messages: list[dict[str, str]]) -> list[dict[str, Any]]:
    """从 messages 列表推导 prompt_summary

    每条消息提取 role / content_length / contains 标签。
    contains 标签检测 content 中的标记：<user-input> / <log> / <retrieval> / rule_hint
    """
    summary = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        content_length = len(content)

        # 推导 contains 标签
        contains = []
        if "<user-input>" in content or "<user-log>" in content:
            contains.append("user_input")
        if "<log>" in content or "<log_snippet>" in content:
            contains.append("log_snippets")
        if "<retrieval>" in content or "相似案例" in content:
            contains.append("rag_context")
        if "rule_hint" in content.lower() or "规则提示" in content:
            contains.append("rule_hint")
        if "<system-resource>" in content or "系统资源" in content:
            contains.append("system_resources")
        if "<network>" in content or "网络" in content:
            contains.append("network_checks")

        # template_hash：system 角色的 content 前 64 字符 hash 作为模板标识
        template_hash = None
        if role == "system" and content:
            import hashlib
            template_hash = hashlib.md5(content[:64].encode()).hexdigest()[:8]

        summary.append({
            "role": role,
            "content_length": content_length,
            "contains": contains,
            "template_hash": template_hash,
        })
    return summary


def truncate_completion(completion: str) -> tuple[str, bool]:
    """截断 completion 到 8KB 上限

    Returns:
        (truncated_text, was_truncated)
    """
    encoded = completion.encode("utf-8", errors="replace")
    if len(encoded) <= _MAX_COMPLETION_BYTES:
        return completion, False
    truncated = encoded[:_MAX_COMPLETION_BYTES].decode("utf-8", errors="replace")
    return truncated + "\n[...truncated]", True


def truncate_output_summary(output: Any, max_bytes: int = _MAX_OUTPUT_SUMMARY_INIT_BYTES) -> str:
    """将 Tool 输出转为字符串摘要并截断

    用于 _safe_collect 入口拦截时记录初始摘要（build_raw_summary 结果在后续回填）。
    """
    try:
        text = repr(output)
    except Exception:
        text = "<unrepresentable>"
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="replace") + "\n[...truncated]"
