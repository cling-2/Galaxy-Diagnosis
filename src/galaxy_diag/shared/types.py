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


class ContainerRuntime(str, Enum):
    """容器运行时子类型

    仅当 env_type == CONTAINER 时有意义，区分 Docker / Kubernetes 采集策略。
    对齐 Environment_awareness_design.md §容器运行时子类型识别。
    """

    DOCKER = "docker"          # 纯 Docker / Podman 容器
    KUBERNETES = "kubernetes"  # Kubernetes Pod
    UNKNOWN = "unknown"        # 识别为容器但运行时无法确定


@dataclass
class DiskInfo:
    """磁盘信息"""

    type: str = ""       # SSD / HDD / NVMe
    capacity: str = ""   # 如 "500GB"
    model: str = ""      # 设备型号


@dataclass
class RaidCardInfo:
    """RAID 卡信息"""

    model: str = ""               # RAID 卡型号
    firmware_version: str = ""    # 固件版本（REQ-B-02 明确要求）


@dataclass
class NicInfo:
    """网卡信息"""

    model: str = ""    # 网卡型号
    driver: str = ""   # 驱动模块


@dataclass
class HardwareInfo:
    """硬件基本信息"""

    cpu_model: str = ""
    cpu_cores: int = 0
    memory_total_gb: float = 0.0
    disks: list[DiskInfo] = field(default_factory=list)
    raid_cards: list[RaidCardInfo] = field(default_factory=list)
    nics: list[NicInfo] = field(default_factory=list)


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
    container_runtime: ContainerRuntime | None = None  # 容器运行时子类型（仅 CONTAINER 时有值）
    hardware: HardwareInfo = field(default_factory=HardwareInfo)
    storage: list[StorageInfo] = field(default_factory=list)
    collection_warnings: list[str] = field(default_factory=list)  # 采集受限/降级提示
    raw_output: dict = field(default_factory=dict)  # 原始采集数据（供 LLM 上下文）


# ===== 诊断信息采集 =====


@dataclass
class LogSnippet:
    """日志片段"""

    source: str = ""           # 来源（如 "kubelet" / "/var/log/dmesg" / "user_upload"）
    level: str = ""            # 级别（ERROR/Warning/Info）
    timestamp: str = ""        # 时间窗标注
    content: str = ""          # 预处理后的日志内容
    truncated: bool = False    # 是否已截断


@dataclass
class DiagnosticContext:
    """COLLECTING → DIAGNOSING 的结构化诊断上下文"""

    problem_description: str = ""                  # 用户问题描述（含补充）
    env_info_ref: EnvironmentType = EnvironmentType.BARE_METAL  # 引用环境类型（env_info 本身在 state 中）
    container_runtime: ContainerRuntime | None = None  # 容器运行时子类型（仅 CONTAINER 时有值）
    component_status: list[dict] = field(default_factory=list)  # 组件部署状态 [{name, status, detail}]
    log_snippets: list[LogSnippet] = field(default_factory=list)  # 日志片段
    system_resources: dict = field(default_factory=dict)  # CPU/MEM/磁盘/负载
    network_checks: list[dict] = field(default_factory=list)  # 连通性检测结果 [{target, reachable, detail}]
    user_provided: list[str] = field(default_factory=list)  # 被动接收的用户日志/描述
    collection_warnings: list[str] = field(default_factory=list)  # 采集降级提示
    raw_output: dict = field(default_factory=dict)  # 预处理后摘要（供 LLM 上下文）
    collected_tools: list[str] = field(default_factory=list)  # 实际调用的 Tool 名（可追溯）


# ===== 诊断分析 =====


class DiagnosisSource(str, Enum):
    """诊断结论来源（异常处理用，见 §异常处理设计）"""

    RULE_MATCH = "rule_match"          # 规则匹配命中
    LLM = "llm"                        # LLM 推理
    LLM_FALLBACK = "llm_fallback"      # LLM 输出校验失败，降级修复后使用
    ERROR_FALLBACK = "error_fallback"  # LLM 调用失败，降级兜底


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
    investigation_steps: list[str] = field(default_factory=list)  # 未知故障的可执行排查步骤
    fault_scope: str = ""                                         # 可能的故障范围描述
    diagnosis_source: DiagnosisSource = DiagnosisSource.LLM       # 结论来源


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
    """工作流步骤

    对应 workflow-design.md §2 完整状态机。
    终态由 history 末条 result 判断，不设枚举值。
    """

    ENV_RECOGNISING = "env_recognising"      # 环境感知
    COLLECTING = "collecting"                # 信息采集
    DIAGNOSING = "diagnosing"                # 根因分析
    PLANNING = "planning"                    # 修复建议生成
    SECURITY_CHECKING = "security_checking"  # 安全检测
    REVIEWING = "reviewing"                  # 人工审核
    SNAPSHOT = "snapshot"                    # 创建恢复快照
    EXECUTING = "executing"                  # 执行修复
    VERIFYING = "verifying"                  # 结果验证

    @property
    def is_terminal(self) -> bool:
        """是否为终态（DONE / REJECTED / ROLLBACK）

        终态通过 history 末条记录判断，而非枚举值。
        当 current_step 不在活跃步骤中且 history 标记完成时，视为终态。
        """
        return False  # 所有枚举值都是活跃步骤


# 会话终态标签（不在 WorkflowStep 枚举中，由 history 判断）
class SessionStatus(str, Enum):
    """会话生命周期状态"""

    ACTIVE = "active"        # 进行中（current_step 为活跃步骤）
    DONE = "done"            # 已完成
    REJECTED = "rejected"    # 已拒绝
    ROLLED_BACK = "rolled_back"  # 已回滚


@dataclass
class WorkflowState:
    """工作流持久化状态

    对齐 workflow-design.md §3，此为唯一状态结构。
    """

    session_id: str = ""
    current_step: WorkflowStep = WorkflowStep.ENV_RECOGNISING
    problem_description: str = ""
    env_info: EnvInfo | None = None
    diagnostic_context: DiagnosticContext | None = None   # COLLECTING 产出（C-01）
    diagnosis: DiagnosisResult | None = None
    fix: FixProposal | None = None
    snapshot: SnapshotMeta | None = None
    history: list[dict] = field(default_factory=list)  # 步骤历史（含时间戳、状态转换、结果）

    @property
    def session_status(self) -> SessionStatus:
        """根据 history 判断会话生命周期状态"""
        if self.history:
            last = self.history[-1]
            result = last.get("result", "")
            if result == "done":
                return SessionStatus.DONE
            if result == "rejected":
                return SessionStatus.REJECTED
            if result == "rollback":
                return SessionStatus.ROLLED_BACK
        return SessionStatus.ACTIVE
