"""操作快照与回滚（REQ-E-03）

执行前自动创建恢复快照：备份受影响的配置文件到 .bak/ 目录，
记录受影响服务的运行状态，写入快照元数据 JSON。

提供一键回滚命令：从备份恢复原始文件，重启受影响服务。

对齐 Safety_design.md §操作快照与回滚设计。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from galaxy_diag.shared.errors import SafetyError
from galaxy_diag.shared.types import RollbackResult, SnapshotMeta

if TYPE_CHECKING:
    from galaxy_diag.shared.types import FixProposal


# 快照存储根目录
_SNAPSHOTS_DIR = Path.home() / ".galaxy-diag" / "snapshots"

# 服务状态记录命令模板
_SERVICE_STATUS_COMMANDS = {
    "systemctl": "systemctl status {service} --no-pager -l",
    "docker": "docker inspect {service}",
}


def _generate_snapshot_id() -> str:
    """生成快照 ID"""
    return f"snap_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def _get_service_status(service: str) -> str:
    """记录单个服务的当前状态"""
    # 优先尝试 systemctl
    for cmd_template in _SERVICE_STATUS_COMMANDS.values():
        try:
            result = subprocess.run(
                cmd_template.format(service=service).split(),
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.stdout + result.stderr
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue
    return ""


def create_snapshot(proposal: "FixProposal", *, session_id: str = "") -> SnapshotMeta:
    """创建恢复快照 (E-03)

    备份受影响的配置文件，记录服务状态，写入元数据 JSON。

    Args:
        proposal: 待执行的修复建议
        session_id: 关联的工作流会话 ID

    Returns:
        SnapshotMeta: 快照元数据

    Raises:
        SafetyError: 快照创建失败（如磁盘空间不足）
    """
    snapshot_id = _generate_snapshot_id()
    snapshot_dir = _SNAPSHOTS_DIR / snapshot_id
    backup_dir = snapshot_dir / "bak"

    try:
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        backup_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise SafetyError(
            f"快照目录创建失败: {e}",
            hint="请检查磁盘空间和 ~/.galaxy-diag/ 目录权限",
        )

    # 收集受影响的文件路径
    affected_files: list[str] = []
    for cmd in proposal.commands:
        # 从命令中提取绝对路径作为可能受影响的文件
        parts = cmd.command.split()
        for part in parts:
            if part.startswith("/") and not part.startswith("/dev/"):
                if os.path.exists(part):
                    affected_files.append(part)

    # 备份受影响的配置文件
    backed_up: list[str] = []
    for filepath in affected_files:
        try:
            dest = backup_dir / filepath.lstrip("/")
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(filepath, dest)
            backed_up.append(filepath)
        except (OSError, PermissionError):
            # 单个文件备份失败不阻止整个快照创建，记录即可
            continue

    # 记录受影响服务状态
    affected_services: list[str] = []
    for cmd in proposal.commands:
        # 提取 systemctl/docker 后的服务名
        parts = cmd.command.split()
        for i, part in enumerate(parts):
            if part in ("systemctl", "docker") and i + 2 < len(parts):
                service = parts[i + 2]
                if service not in affected_services:
                    affected_services.append(service)
                    status = _get_service_status(service)
                    if status:
                        status_file = snapshot_dir / f"service_{service}.txt"
                        try:
                            status_file.write_text(status, encoding="utf-8")
                        except OSError:
                            pass

    # 生成操作摘要
    cmd_count = len(proposal.commands)
    summary = f"备份 {len(backed_up)} 个文件，记录 {len(affected_services)} 个服务状态（共 {cmd_count} 条命令）"

    # 构建元数据
    meta = SnapshotMeta(
        snapshot_id=snapshot_id,
        timestamp=datetime.now(),
        operation_summary=summary,
        affected_files=backed_up,
        affected_services=affected_services,
        backup_path=str(backup_dir),
    )

    # 写入元数据 JSON
    meta_file = snapshot_dir / "meta.json"
    try:
        meta_file.write_text(
            json.dumps(
                {
                    "snapshot_id": meta.snapshot_id,
                    "timestamp": meta.timestamp.isoformat() if meta.timestamp else "",
                    "operation_summary": meta.operation_summary,
                    "affected_files": meta.affected_files,
                    "affected_services": meta.affected_services,
                    "backup_path": meta.backup_path,
                    "session_id": session_id,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError as e:
        raise SafetyError(
            f"快照元数据写入失败: {e}",
            hint="请检查磁盘空间",
        )

    return meta


def rollback(snapshot_id: str) -> RollbackResult:
    """一键回滚 (E-03)

    从快照恢复原始文件，重启受影响服务。

    Args:
        snapshot_id: 快照 ID

    Returns:
        RollbackResult: 回滚结果

    Raises:
        SafetyError: 快照不存在或回滚失败
    """
    snapshot_dir = _SNAPSHOTS_DIR / snapshot_id
    backup_dir = snapshot_dir / "bak"
    meta_file = snapshot_dir / "meta.json"

    if not snapshot_dir.exists():
        raise SafetyError(
            f"快照不存在: {snapshot_id}",
            hint="请使用 galaxy-diag snapshot list 查看可用快照",
        )

    # 读取元数据获取受影响文件列表
    affected_files: list[str] = []
    if meta_file.exists():
        try:
            raw = json.loads(meta_file.read_text(encoding="utf-8"))
            affected_files = raw.get("affected_files", [])
        except (json.JSONDecodeError, OSError):
            pass

    # 从备份恢复原始文件
    restored: list[str] = []
    for filepath in affected_files:
        src = backup_dir / filepath.lstrip("/")
        if src.exists():
            try:
                shutil.copy2(src, filepath)
                restored.append(filepath)
            except (OSError, PermissionError):
                continue

    # 恢复受影响服务（尝试重启到快照时的状态）
    services_restarted: list[str] = []
    for service_file in snapshot_dir.glob("service_*.txt"):
        service_name = service_file.stem.replace("service_", "")
        try:
            subprocess.run(
                ["systemctl", "restart", service_name],
                capture_output=True,
                text=True,
                timeout=30,
            )
            services_restarted.append(service_name)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue

    all_restored = len(restored) == len(affected_files)
    message = f"已恢复 {len(restored)}/{len(affected_files)} 个文件，重启 {len(services_restarted)} 个服务"
    if not all_restored:
        message += f"（{len(affected_files) - len(restored)} 个文件恢复失败）"

    return RollbackResult(
        success=all_restored,
        restored_files=restored,
        message=message,
    )


def list_snapshots() -> list[SnapshotMeta]:
    """列出所有可用快照

    Returns:
        快照元数据列表（按时间倒序）
    """
    results: list[SnapshotMeta] = []
    if not _SNAPSHOTS_DIR.exists():
        return results

    for snapshot_dir in _SNAPSHOTS_DIR.iterdir():
        if not snapshot_dir.is_dir():
            continue
        meta_file = snapshot_dir / "meta.json"
        if not meta_file.exists():
            continue
        try:
            raw = json.loads(meta_file.read_text(encoding="utf-8"))
            meta = SnapshotMeta(
                snapshot_id=raw.get("snapshot_id", ""),
                timestamp=datetime.fromisoformat(raw["timestamp"]) if raw.get("timestamp") else None,
                operation_summary=raw.get("operation_summary", ""),
                affected_files=raw.get("affected_files", []),
                affected_services=raw.get("affected_services", []),
                backup_path=raw.get("backup_path", ""),
            )
            results.append(meta)
        except (json.JSONDecodeError, OSError, KeyError):
            continue

    # 按时间倒序
    results.sort(key=lambda m: m.timestamp or datetime.min, reverse=True)
    return results
