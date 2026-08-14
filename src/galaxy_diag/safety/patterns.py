"""危险命令模式库（REQ-E-02 数据层）

只定义数据，不含匹配逻辑。安全/SRE 团队可独立维护模式库
（新增/调整危险命令），无需修改 danger.py 的匹配算法。

与 fixer D-03 的危险模式（fixer/checker.py DANGER_PATTERNS_ADVISORY）互补：
- D-03 危险模式为 WARNING 建议性提醒（不阻止）
- E-02 本模块为 CRITICAL/WARNING 强制拦截（进入 CONFIRM 流程）

对齐 Safety_design.md §危险命令模式库设计。
"""

from __future__ import annotations

from galaxy_diag.shared.types import CheckSeverity, DangerPattern


# 危险命令模式库
# 按 data_loss / privilege / network / system 四类组织，对齐任务书 REQ-E-02 验收标准
DANGER_PATTERNS: list[DangerPattern] = [
    # ===== 数据破坏类 =====
    DangerPattern(
        pattern=r"\brm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+|.*--no-preserve-root)",
        category="data_loss",
        severity=CheckSeverity.CRITICAL,
        description="危险删除（rm -rf 或 --no-preserve-root）",
        suggestion="请确认删除目标路径，避免误删根目录或系统文件",
    ),
    DangerPattern(
        pattern=r"\bmkfs\b",
        category="data_loss",
        severity=CheckSeverity.CRITICAL,
        description="文件系统格式化（mkfs）",
        suggestion="格式化将清除磁盘全部数据，请确认目标设备路径正确",
    ),
    DangerPattern(
        pattern=r"\bdd\b.*\bof\s*=\s*/dev/",
        category="data_loss",
        severity=CheckSeverity.CRITICAL,
        description="直接写入块设备（dd of=/dev/...）",
        suggestion="直接写块设备可能破坏分区表，请确认目标设备",
    ),
    DangerPattern(
        pattern=r":\(\)\s*\{.*\|.*\&\s*\};",
        category="data_loss",
        severity=CheckSeverity.CRITICAL,
        description="Fork 炸弹模式",
        suggestion="检测到 Fork 炸弹模式，将耗尽系统资源",
    ),

    # ===== 权限变更类 =====
    DangerPattern(
        pattern=r"\bchmod\s+777\b",
        category="privilege",
        severity=CheckSeverity.WARNING,
        description="过度宽松权限（chmod 777）",
        suggestion="777 权限对所有用户开放读写执行，建议使用更严格的权限",
    ),
    DangerPattern(
        pattern=r"\bchown\s+-R\b.*\s+/\s*$|\bchown\s+-R\b.*\s+/\s+",
        category="privilege",
        severity=CheckSeverity.CRITICAL,
        description="递归变更根目录属主（chown -R /）",
        suggestion="递归变更根目录属主将破坏整个系统权限体系",
    ),
    DangerPattern(
        pattern=r"\bchmod\s+-R\b.*\s+/\s*$",
        category="privilege",
        severity=CheckSeverity.CRITICAL,
        description="递归变更根目录权限（chmod -R /）",
        suggestion="递归变更根目录权限将破坏整个系统权限体系",
    ),

    # ===== 网络安全类 =====
    DangerPattern(
        pattern=r"\biptables\s+-F\b|\biptables\s+-X\b",
        category="network",
        severity=CheckSeverity.CRITICAL,
        description="清空防火墙规则（iptables -F/-X）",
        suggestion="清空防火墙规则将导致系统失去网络访问控制，可能引发安全风险",
    ),
    DangerPattern(
        pattern=r"\bfirewall-cmd\s+--reload\b.*--permanent",
        category="network",
        severity=CheckSeverity.WARNING,
        description="重载永久防火墙规则",
        suggestion="重载永久防火墙规则可能影响现有网络连接",
    ),

    # ===== 系统关键类 =====
    DangerPattern(
        pattern=r"\bsystemctl\s+(stop|disable)\s+(sshd|docker|kubelet)\b",
        category="system",
        severity=CheckSeverity.WARNING,
        description="停止关键服务（sshd/docker/kubelet）",
        suggestion="停止关键服务可能导致远程连接断开或集群不可用",
    ),
    DangerPattern(
        pattern=r"\breboot\b|\bshutdown\b|\bpoweroff\b",
        category="system",
        severity=CheckSeverity.WARNING,
        description="重启或关机",
        suggestion="重启/关机将中断所有正在进行的操作",
    ),
    DangerPattern(
        pattern=r"\bkill\s+-9\s+-?1\b",
        category="system",
        severity=CheckSeverity.CRITICAL,
        description="杀死所有进程（kill -9 -1）",
        suggestion="kill -9 -1 将终止所有可终止进程，导致系统不可用",
    ),
]
