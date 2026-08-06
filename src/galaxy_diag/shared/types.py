"""跨域核心数据结构

每个域的输出就是下一个域的输入，此处定义全部跨域契约。
对应架构设计 §5。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Literal


# ===== 环境感知 =====


class EnvironmentType(str, Enum):
    """运行环境类型"""

    BARE_METAL = "bare_metal"
    VM = "vm"
    CONTAINER = "container"


@dataclass
class HardwareInfo:
    """硬件基本信息"""

    cpu_model: str = ""
    cpu_cores: int = 0
    memory_total_gb: float = 0.0
    disks: list[dict] = field(default_factory=list)      # [{type, capacity, model}]
    raid_cards: list[dict] = field(default_factory=list)  # [{model, firmware_version}]
    nics: list[dict] = field(default_factory=list)        # [{model, driver}]


@dataclass
class StorageInfo:
    """第三方存储设备信息"""

    storage_type: Literal["SAN", "NAS", "local"] = "local"
    mount_path: str = ""
    filesystem: str = ""
    details: dict = field(default_factory=dict)


@dataclass
class EnvInfo:
    """collector → diagnoser 的采集结果"""

    env_type: EnvironmentType = EnvironmentType.BARE_METAL
    hardware: HardwareInfo = field(default_factory=HardwareInfo)
    storage: list[StorageInfo] = field(default_factory=list)
    raw_output: dict = field(default_factory=dict)  # 原始采集数据（供 LLM 上下文）


# ===== 诊断分析 =====


class Confidence(str, Enum):
    """诊断结论置信度"""

    CONFIRMED = "confirmed"        # 已确认
    SUSPECTED = "suspected"        # 推测
    INSUFFICIENT = "insufficient"  # 信息不足


@dataclass
class DiagnosisResult:
    """diagnoser → fixer 的诊断结论"""

    root_cause: str = ""
    confidence: Confidence = Confidence.INSUFFICIENT
    missing_info: list[str] = field(default_factory=list)  # 信息不足时，列出缺失项
    evidence: list[str] = field(default_factory=list)       # 支撑结论的证据
    env_type: EnvironmentType = EnvironmentType.BARE_METAL


# ===== 修复生成 =====


@dataclass
class CommandTemplate:
    """单条命令模板"""

    command: str = ""  # 含占位符如 <IP>, <MOUNT_POINT>
    description: str = ""
    risk_note: str = ""  # 安全风险提示
    editable_params: dict[str, str] = field(default_factory=dict)  # 占位符名 → 默认值


@dataclass
class FixProposal:
    """fixer → safety 的修复建议"""

    commands: list[CommandTemplate] = field(default_factory=list)
    script: str | None = None  # 多步骤脚本内容（可选）
    script_language: Literal["bash", "python"] | None = None
    risk_notes: list[str] = field(default_factory=list)  # 整体风险提示
    check_passed: bool = False  # 多维检测是否通过
    check_issues: list[str] = field(default_factory=list)  # 检测发现的问题
    impact_scope: str = ""  # 影响范围描述


# ===== 安全可控 =====


@dataclass
class SnapshotMeta:
    """快照元数据"""

    snapshot_id: str = ""
    timestamp: datetime | None = None
    operation_summary: str = ""
    affected_files: list[str] = field(default_factory=list)
    affected_services: list[str] = field(default_factory=list)
    backup_path: str = ""


@dataclass
class AuditRecord:
    """审计日志记录"""

    timestamp: datetime | None = None
    session_id: str = ""
    operator: str = ""
    action: str = ""
    result: Literal["success", "failure", "rollback", "rejected"] = "success"
    llm_basis: str = ""  # LLM 分析依据摘要
    snapshot_id: str | None = None  # 关联的快照 ID
    user_input: str = ""  # 用户确认输入（y / N / CONFIRM xxx）


# ===== 工作流 =====


class WorkflowStep(str, Enum):
    """工作流步骤"""

    COLLECT = "collect"
    DIAGNOSE = "diagnose"
    FIX = "fix"
    REVIEW = "review"
    EXECUTE = "execute"
    VERIFY = "verify"


@dataclass
class WorkflowState:
    """工作流持久化状态"""

    session_id: str = ""
    current_step: WorkflowStep = WorkflowStep.COLLECT
    problem_description: str = ""
    env_info: EnvInfo | None = None
    diagnosis: DiagnosisResult | None = None
    fix: FixProposal | None = None
    snapshot: SnapshotMeta | None = None
    history: list[dict] = field(default_factory=list)  # 步骤历史（含时间戳和结果）
