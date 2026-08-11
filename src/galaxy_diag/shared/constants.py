"""领域知识常量

银河平台组件清单、关键日志路径、环境类型标签等。
对应架构设计 shared/constants.py，预置领域知识供各域复用。
"""

from __future__ import annotations

from galaxy_diag.shared.types import EnvironmentType

# 环境类型中文标签
ENV_TYPE_LABELS: dict[EnvironmentType, str] = {
    EnvironmentType.BARE_METAL: "裸金属",
    EnvironmentType.VM: "虚拟机",
    EnvironmentType.CONTAINER: "容器",
}

# 诊断结论置信度中文标签
CONFIDENCE_LABELS: dict[str, str] = {
    "confirmed": "已确认",
    "suspected": "推测",
    "insufficient": "信息不足",
}

# 审计日志结果中文标签
AUDIT_RESULT_LABELS: dict[str, str] = {
    "success": "成功",
    "failure": "失败",
    "rollback": "已回滚",
    "rejected": "已拒绝",
}

# 银河平台关键组件清单（供诊断采集工具预置）
GALAXY_COMPONENTS: list[str] = [
    "galaxy-compute",      # 计算服务
    "galaxy-network",      # 网络服务
    "galaxy-storage",      # 存储服务
    "galaxy-control",      # 控制面
    "galaxy-scheduler",    # 调度器
    "galaxy-api",          # API 网关
]

# 关键日志路径（供采集工具预置）
KEY_LOG_PATHS: dict[str, str] = {
    "system": "/var/log/syslog",
    "dmesg": "/var/log/dmesg",
    "kubelet": "/var/log/kubelet.log",
    "docker": "/var/log/docker.log",
    "galaxy-control": "/var/log/galaxy/control.log",
    "galaxy-network": "/var/log/galaxy/network.log",
    "galaxy-storage": "/var/log/galaxy/storage.log",
    "messages": "/var/log/messages",
}

# 容器运行时子类型中文标签
CONTAINER_RUNTIME_LABELS: dict[str, str] = {
    "docker": "Docker",
    "kubernetes": "Kubernetes",
    "unknown": "未知",
}

# 工具名与版本
TOOL_NAME = "galaxy-diag"
TOOL_VERSION = "0.1.0"
