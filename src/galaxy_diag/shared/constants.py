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

# 诊断结论来源中文标签
DIAGNOSIS_SOURCE_LABELS: dict[str, str] = {
    "rule_match": "规则匹配",
    "llm": "LLM 推理",
    "llm_fallback": "LLM 降级",
    "error_fallback": "降级兜底",
}

# 审计日志结果中文标签
AUDIT_RESULT_LABELS: dict[str, str] = {
    "success": "成功",
    "failure": "失败",
    "rollback": "已回滚",
    "rejected": "已拒绝",
    "confirmed": "已确认",
    "verify_failed": "验证失败",
}

# 银河平台关键组件清单（供诊断采集工具预置）
# 测试容器（test-sandbox, 179eba426a53）内真实运行的服务进程名。
# 容器内无 docker/kubectl/systemctl，采集走进程树扫描（/proc/<pid>/cmdline 匹配），
# 故此处须为进程命令行中可直接匹配到的字符串。
GALAXY_COMPONENTS: list[str] = [
    "nginx",   # 前端 / API 网关（nginx master + workers）
    "ollama",  # LLM 推理服务（/usr/local/bin/ollama serve）
]

# 关键日志路径（供采集工具预置）
# 测试容器内真实存在的日志文件。
# name 为日志标识，path 为容器内绝对路径；采集时读尾部并按关键词过滤。
#
# 注意：nginx 日志 /var/log/nginx/{error,access}.log 在 Docker 容器中是
# 指向 /dev/stdout、/dev/stderr 的符号链接（Docker 标准日志模式），
# open() 读模式会阻塞挂起，故不在此预置；其日志经 docker logs 查看，
# 容器内无 docker CLI 时无法采集（属环境限制，非阻塞）。
KEY_LOG_PATHS: dict[str, str] = {
    "ollama": "/var/log/galaxy-diag/ollama.log",       # Ollama LLM 服务日志
    # 故障注入：测试环境专用的故障模拟机制
    # /var/log/.fault-fill-* 为磁盘填充故障，/var/log/.fault-inode-* 为 inode 耗尽故障
    "fault-inject-os-01": "/run/fault-inject/os-01.log",
    "fault-inject-os-02": "/run/fault-inject/os-02.log",
}

# 容器运行时子类型中文标签
CONTAINER_RUNTIME_LABELS: dict[str, str] = {
    "docker": "Docker",
    "kubernetes": "Kubernetes",
    "unknown": "未知",
}

# 修复建议来源中文标签
FIX_SOURCE_LABELS: dict[str, str] = {
    "llm": "LLM 生成",
    "llm_fallback": "LLM 降级",
    "error_fallback": "降级兜底",
}

# 检测严重级别中文标签
CHECK_SEVERITY_LABELS: dict[str, str] = {
    "critical": "严重",
    "warning": "警告",
    "info": "提示",
}

# 检测维度中文标签
CHECK_CATEGORY_LABELS: dict[str, str] = {
    "syntax": "语法",
    "danger": "危险",
    "compatibility": "兼容性",
}

# 工具名与版本
TOOL_NAME = "galaxy-diag"
TOOL_VERSION = "0.1.0"
