"""工作流状态持久化与恢复

对应 workflow-design.md §4。
存储位置: ~/.galaxy-diag/sessions/<session_id>.json
每个状态转换完成后立即落盘，确保任意时刻崩溃可恢复。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, fields
from datetime import datetime
from pathlib import Path
from typing import Any

from galaxy_diag.shared.errors import WorkflowError
from galaxy_diag.shared.types import (
    SessionStatus,
    WorkflowStep,
    WorkflowState,
)


# 默认会话存储根目录
DEFAULT_SESSION_DIR = Path.home() / ".galaxy-diag" / "sessions"


def _session_dir() -> Path:
    """获取会话存储目录（可通过 GALAXY_SESSION_DIR 环境变量覆盖）"""
    override = os.environ.get("GALAXY_SESSION_DIR")
    if override:
        return Path(override)
    return DEFAULT_SESSION_DIR


def generate_session_id() -> str:
    """生成会话 ID，格式: sess_YYYYMMDD_HHMMSS"""
    now = datetime.now()
    return f"sess_{now.strftime('%Y%m%d_%H%M%S')}"


def save_state(state: WorkflowState) -> Path:
    """将 WorkflowState 序列化为 JSON 写入磁盘

    每次状态转换后调用，确保持久化。

    Args:
        state: 当前工作流状态

    Returns:
        保存的文件路径

    Raises:
        WorkflowError: 写入失败
    """
    session_dir = _session_dir()
    session_dir.mkdir(parents=True, exist_ok=True)

    file_path = session_dir / f"{state.session_id}.json"

    try:
        raw = _state_to_dict(state)
        file_path.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except (OSError, TypeError) as e:
        raise WorkflowError(
            f"保存会话状态失败: {state.session_id}",
            hint=f"请确认目录可写: {session_dir}。错误: {e}",
        )

    return file_path


def load_state(session_id: str) -> WorkflowState:
    """从磁盘加载 WorkflowState

    Args:
        session_id: 会话 ID

    Returns:
        加载的 WorkflowState

    Raises:
        WorkflowError: 文件不存在、JSON 损坏、字段缺失
    """
    file_path = _session_dir() / f"{session_id}.json"

    if not file_path.exists():
        raise WorkflowError(
            f"会话不存在: {session_id}",
            hint=f"请确认会话 ID 正确。会话目录: {_session_dir()}",
        )

    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise WorkflowError(
            f"会话文件损坏: {session_id}",
            hint=f"请删除损坏的会话文件后重试: {file_path}。错误: {e}",
        )

    try:
        return _dict_to_state(raw)
    except (KeyError, ValueError) as e:
        raise WorkflowError(
            f"会话数据格式错误: {session_id}",
            hint=f"会话文件可能由新版本生成，不兼容当前版本。错误: {e}",
        )


def list_sessions() -> list[dict[str, Any]]:
    """列出所有会话的摘要信息

    Returns:
        会话摘要列表，每项含 session_id, current_step, problem_description, session_status
    """
    session_dir = _session_dir()
    if not session_dir.exists():
        return []

    results: list[dict[str, Any]] = []
    for path in sorted(session_dir.glob("sess_*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            state = _dict_to_state(raw)
            results.append({
                "session_id": state.session_id,
                "current_step": state.current_step.value,
                "problem_description": state.problem_description[:60] + "..."
                if len(state.problem_description) > 60
                else state.problem_description,
                "session_status": state.session_status.value,
            })
        except (json.JSONDecodeError, KeyError, ValueError):
            # 跳过损坏的会话文件
            results.append({
                "session_id": path.stem,
                "current_step": "unknown",
                "problem_description": "(会话文件损坏)",
                "session_status": "unknown",
            })

    return results


def find_resumable_sessions() -> list[WorkflowState]:
    """查找所有可恢复的未完成会话

    Returns:
        状态为 ACTIVE 的 WorkflowState 列表
    """
    session_dir = _session_dir()
    if not session_dir.exists():
        return []

    results: list[WorkflowState] = []
    for path in sorted(session_dir.glob("sess_*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            state = _dict_to_state(raw)
            if state.session_status == SessionStatus.ACTIVE:
                results.append(state)
        except (json.JSONDecodeError, KeyError, ValueError):
            continue

    return results


def _state_to_dict(state: WorkflowState) -> dict[str, Any]:
    """将 WorkflowState 序列化为可 JSON 化的 dict

    处理 Enum 和 datetime 的特殊序列化。
    """
    raw = asdict(state)
    # WorkflowStep 枚举 → 字符串
    raw["current_step"] = state.current_step.value
    # 处理嵌套的 datetime（SnapshotMeta.timestamp）
    _convert_datetimes(raw)
    return raw


def _convert_datetimes(obj: Any) -> None:
    """递归将 dict/list 中的 datetime 对象转为 ISO 字符串"""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(value, datetime):
                obj[key] = value.isoformat()
            elif isinstance(value, (dict, list)):
                _convert_datetimes(value)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            if isinstance(item, datetime):
                obj[i] = item.isoformat()
            elif isinstance(item, (dict, list)):
                _convert_datetimes(item)



def _dict_to_state(raw: dict[str, Any]) -> WorkflowState:
    """将 dict 反序列化为 WorkflowState

    处理 Enum 的特殊反序列化，并过滤未知字段（向前兼容）。
    """
    # current_step 字符串 → 枚举
    step_str = raw.get("current_step", "")
    try:
        current_step = WorkflowStep(step_str)
    except ValueError:
        raise ValueError(f"未知的步骤值: {step_str!r}")

    # 提取已知字段，忽略未知字段（向前兼容）
    state = WorkflowState(
        session_id=raw.get("session_id", ""),
        current_step=current_step,
        problem_description=raw.get("problem_description", ""),
        env_info=_parse_env_info(raw.get("env_info")),
        diagnosis=_parse_diagnosis(raw.get("diagnosis")),
        fix=_parse_fix_proposal(raw.get("fix")),
        snapshot=_parse_snapshot_meta(raw.get("snapshot")),
        history=raw.get("history", []),
    )
    return state


# ===== 嵌套类型解析 =====
# 使用动态导入避免循环依赖，同时保持 types.py 作为唯一定义源


def _parse_env_info(raw: dict | None) -> Any:
    """解析 EnvInfo（可能为 None）"""
    if raw is None:
        return None
    from galaxy_diag.shared.types import EnvInfo, EnvironmentType, HardwareInfo, StorageInfo

    # 解析 env_type 枚举
    env_type_str = raw.get("env_type", "bare_metal")
    try:
        env_type = EnvironmentType(env_type_str)
    except ValueError:
        env_type = EnvironmentType.BARE_METAL

    # 解析 hardware
    hw_raw = raw.get("hardware", {})
    hardware = HardwareInfo(
        cpu_model=hw_raw.get("cpu_model", ""),
        cpu_cores=hw_raw.get("cpu_cores", 0),
        memory_total_gb=hw_raw.get("memory_total_gb", 0.0),
        disks=hw_raw.get("disks", []),
        raid_cards=hw_raw.get("raid_cards", []),
        nics=hw_raw.get("nics", []),
    )

    # 解析 storage 列表
    storage_list = []
    for st_raw in raw.get("storage", []):
        storage_list.append(StorageInfo(
            storage_type=st_raw.get("storage_type", "local"),
            mount_path=st_raw.get("mount_path", ""),
            filesystem=st_raw.get("filesystem", ""),
            details=st_raw.get("details", {}),
        ))

    return EnvInfo(
        env_type=env_type,
        hardware=hardware,
        storage=storage_list,
        raw_output=raw.get("raw_output", {}),
    )


def _parse_diagnosis(raw: dict | None) -> Any:
    """解析 DiagnosisResult（可能为 None）"""
    if raw is None:
        return None
    from galaxy_diag.shared.types import Confidence, DiagnosisResult, EnvironmentType

    conf_str = raw.get("confidence", "insufficient")
    try:
        confidence = Confidence(conf_str)
    except ValueError:
        confidence = Confidence.INSUFFICIENT

    env_type_str = raw.get("env_type", "bare_metal")
    try:
        env_type = EnvironmentType(env_type_str)
    except ValueError:
        env_type = EnvironmentType.BARE_METAL

    return DiagnosisResult(
        root_cause=raw.get("root_cause", ""),
        confidence=confidence,
        missing_info=raw.get("missing_info", []),
        evidence=raw.get("evidence", []),
        env_type=env_type,
    )


def _parse_fix_proposal(raw: dict | None) -> Any:
    """解析 FixProposal（可能为 None）"""
    if raw is None:
        return None
    from galaxy_diag.shared.types import CommandTemplate, FixProposal

    commands = []
    for cmd_raw in raw.get("commands", []):
        commands.append(CommandTemplate(
            command=cmd_raw.get("command", ""),
            description=cmd_raw.get("description", ""),
            risk_note=cmd_raw.get("risk_note", ""),
            editable_params=cmd_raw.get("editable_params", {}),
        ))

    return FixProposal(
        commands=commands,
        script=raw.get("script"),
        script_language=raw.get("script_language"),
        risk_notes=raw.get("risk_notes", []),
        check_passed=raw.get("check_passed", False),
        check_issues=raw.get("check_issues", []),
        impact_scope=raw.get("impact_scope", ""),
    )


def _parse_snapshot_meta(raw: dict | None) -> Any:
    """解析 SnapshotMeta（可能为 None）"""
    if raw is None:
        return None
    from galaxy_diag.shared.types import SnapshotMeta

    ts_str = raw.get("timestamp")
    timestamp = None
    if ts_str and isinstance(ts_str, str):
        try:
            timestamp = datetime.fromisoformat(ts_str)
        except ValueError:
            timestamp = None

    return SnapshotMeta(
        snapshot_id=raw.get("snapshot_id", ""),
        timestamp=timestamp,
        operation_summary=raw.get("operation_summary", ""),
        affected_files=raw.get("affected_files", []),
        affected_services=raw.get("affected_services", []),
        backup_path=raw.get("backup_path", ""),
    )
