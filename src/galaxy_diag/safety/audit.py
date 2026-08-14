"""操作留痕与审计日志（REQ-E-04）

专用函数写入 JSONL，不经 Agent/LLM 输出流。
每次操作后立即写盘（append 模式），不缓存在内存。
Agent 没有修改/删除审计日志的 Tool，防 Prompt 注入篡改。

两阶段留痕：
1. 审核同意后写 result=confirmed（用户确认决策已留痕）
2. 执行完成后再写 result=success/failure/rollback
这样即使执行过程崩溃，用户的"确认"决策仍已记录。

对齐 Safety_design.md §审计日志设计。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from galaxy_diag.shared.types import AuditRecord


# 审计日志文件路径
_AUDIT_LOG_PATH = Path.home() / ".galaxy-diag" / "audit.jsonl"

# 备用路径（主路径写入失败时尝试）
_AUDIT_LOG_FALLBACK_PATH = Path.home() / ".galaxy-diag" / "audit.failed.jsonl"


def _ensure_log_file(path: Path) -> None:
    """确保日志文件和目录存在"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch()


def write_audit(record: AuditRecord) -> None:
    """写入审计日志 (E-04)

    不经 Agent / LLM 输出流。使用 json.dumps() + 文件追加写入。
    Agent 的 Tool 列表中不包含修改/删除审计日志的 Tool，
    防 Prompt 注入篡改日志内容。

    写入失败时尝试备用路径，并打印告警但不抛异常
    （审计不阻塞核心操作）。

    Args:
        record: 审计记录
    """
    line = json.dumps(
        {
            "timestamp": record.timestamp.isoformat() if record.timestamp else "",
            "session_id": record.session_id,
            "operator": record.operator,
            "action": record.action,
            "result": record.result,
            "llm_basis": record.llm_basis,
            "snapshot_id": record.snapshot_id,
            "user_input": record.user_input,
        },
        ensure_ascii=False,
    )

    # 尝试主路径
    try:
        _ensure_log_file(_AUDIT_LOG_PATH)
        with open(_AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        return
    except OSError:
        pass

    # 主路径失败，尝试备用路径
    try:
        _ensure_log_file(_AUDIT_LOG_FALLBACK_PATH)
        with open(_AUDIT_LOG_FALLBACK_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        # 备用路径也失败：打印告警但不抛异常
        # 审计不应阻塞核心操作，但失败必须可见
        import sys
        print(
            f"[审计告警] 审计日志写入失败: action={record.action}, result={record.result}",
            file=sys.stderr,
        )


def query_audit(
    *,
    session_id: str | None = None,
    limit: int = 50,
    since: datetime | None = None,
) -> list[AuditRecord]:
    """查询审计日志 (E-04)

    Args:
        session_id: 按会话 ID 过滤（None 表示不过滤）
        limit: 最多返回记录数
        since: 只返回此时间之后的记录

    Returns:
        审计记录列表（按时间倒序）
    """
    if not _AUDIT_LOG_PATH.exists():
        return []

    records: list[AuditRecord] = []
    try:
        with open(_AUDIT_LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # 过滤
                if session_id and raw.get("session_id") != session_id:
                    continue
                if since:
                    ts_str = raw.get("timestamp", "")
                    if ts_str:
                        try:
                            ts = datetime.fromisoformat(ts_str)
                            if ts < since:
                                continue
                        except ValueError:
                            pass

                record = AuditRecord(
                    timestamp=datetime.fromisoformat(raw["timestamp"]) if raw.get("timestamp") else None,
                    session_id=raw.get("session_id", ""),
                    operator=raw.get("operator", ""),
                    action=raw.get("action", ""),
                    result=raw.get("result", "success"),  # type: ignore[arg-type]
                    llm_basis=raw.get("llm_basis", ""),
                    snapshot_id=raw.get("snapshot_id"),
                    user_input=raw.get("user_input", ""),
                )
                records.append(record)
    except OSError:
        return []

    # 按时间倒序，取前 limit 条
    records.sort(key=lambda r: r.timestamp or datetime.min, reverse=True)
    return records[:limit]


def build_record(
    *,
    session_id: str,
    action: str,
    result: str,
    user_input: str = "",
    llm_basis: str = "",
    snapshot_id: str | None = None,
) -> AuditRecord:
    """便捷构造 AuditRecord

    自动填充 timestamp 和 operator。

    Args:
        session_id: 会话 ID
        action: 操作内容描述
        result: 操作结果（confirmed/success/failure/rollback/rejected）
        user_input: 用户确认输入
        llm_basis: LLM 分析依据摘要
        snapshot_id: 关联的快照 ID

    Returns:
        构造好的 AuditRecord
    """
    operator = ""
    try:
        operator = os.getlogin()
    except OSError:
        operator = "unknown"

    return AuditRecord(
        timestamp=datetime.now(),
        session_id=session_id,
        operator=operator,
        action=action,
        result=result,  # type: ignore[arg-type]
        llm_basis=llm_basis,
        snapshot_id=snapshot_id,
        user_input=user_input,
    )
