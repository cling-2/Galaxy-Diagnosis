"""多维错误检测器（REQ-D-03）

对生成的命令和脚本做多维错误检测，在用户审核前拦截代码质量问题。
D-03 性质为"质量保障"，拦截策略为建议性：
  - 语法错误、环境不兼容 → CRITICAL（阻止进入 REVIEWING）
  - 危险模式提醒 → WARNING（允许继续，告知用户）

确定性纯函数——不依赖 LLM、不修改状态、无副作用。
强制拦截由 safety/danger.py 在 EXECUTION_GUARD 步骤执行。

对应设计文档 §多维错误检测器。
"""

from __future__ import annotations

import re
import subprocess
from typing import TYPE_CHECKING, Literal

from galaxy_diag.fixer.template import PLACEHOLDER_PATTERN
from galaxy_diag.shared.types import (
    CheckIssue,
    CheckResult,
    CheckSeverity,
    EnvironmentType,
)

if TYPE_CHECKING:
    from galaxy_diag.shared.types import CommandTemplate


# ===== 危险模式建议性警告（D-03 专用） =====

# 粒度较粗，目的是"信息展示和教育"，而非"安全拦截"
# 与 safety/patterns.py 中的 DANGER_PATTERNS 互补：
#   D-03 此处 = 通用代码质量提醒（WARNING，不阻止）
#   E-02 safety/patterns.py = 业务安全策略强制拦截（CRITICAL，阻止执行）
_D03_DANGER_ADVISORY_PATTERNS: list[tuple[str, str]] = [
    (r"rm\s+-rf",                     "包含强制删除，请确认目标路径"),
    (r"mkfs\.",                       "包含文件系统格式化，将清除目标分区数据"),
    (r"chmod\s+(777|666)",            "包含过度宽松权限设置"),
    (r"iptables\s+-F",                "包含防火墙规则清空"),
    (r"(password|passwd)\s*=\s*['\"]", "疑似包含明文密码"),
    (r"systemctl\s+restart",          "包含服务重启，将导致短暂中断"),
    (r"reboot",                        "包含系统重启"),
]


# ===== ① 语法检查 =====


def _check_unresolved_placeholders(
    commands: list[CommandTemplate],
    script: str | None,
) -> list[CheckIssue]:
    """检测未替换的占位符

    占位符未替换是最常见的"语法错误"——直接执行会导致命令失败。
    """
    issues: list[CheckIssue] = []

    for i, cmd in enumerate(commands):
        unresolved = PLACEHOLDER_PATTERN.findall(cmd.command)
        if unresolved:
            issues.append(CheckIssue(
                category="syntax",
                severity=CheckSeverity.CRITICAL,
                message=f"步骤 {i+1} 含未替换的占位符: {', '.join(f'<{p}>' for p in unresolved)}",
                command_index=i,
                suggestion=f"请为 {', '.join(f'<{p}>' for p in unresolved)} 填入实际值",
            ))

    if script:
        unresolved = PLACEHOLDER_PATTERN.findall(script)
        if unresolved:
            issues.append(CheckIssue(
                category="syntax",
                severity=CheckSeverity.CRITICAL,
                message=f"脚本含未替换的占位符: {', '.join(f'<{p}>' for p in unresolved)}",
                command_index=-1,
                suggestion="请编辑参数后重新生成",
            ))

    return issues


def _check_bash_syntax(script: str | None) -> list[CheckIssue]:
    """Bash 语法检查

    策略：优先尝试 ShellCheck（如已安装），否则做基本模式匹配。
    不强制依赖 ShellCheck——离线环境可能未安装。
    """
    if not script:
        return []

    issues: list[CheckIssue] = []

    try:
        result = subprocess.run(
            ["shellcheck", "--severity=error", "-"],
            input=script, text=True, capture_output=True, timeout=10,
        )
        if result.returncode != 0:
            for line in result.stdout.strip().split("\n"):
                if line.strip():
                    issues.append(CheckIssue(
                        category="syntax",
                        severity=CheckSeverity.CRITICAL,
                        message=f"ShellCheck: {line.strip()}",
                        command_index=-1,
                    ))
    except FileNotFoundError:
        # ShellCheck 未安装：降级为基本模式匹配
        issues.extend(_basic_bash_check(script))
    except subprocess.TimeoutExpired:
        issues.append(CheckIssue(
            category="syntax",
            severity=CheckSeverity.WARNING,
            message="ShellCheck 执行超时，跳过语法检查",
            command_index=-1,
        ))

    return issues


def _basic_bash_check(script: str) -> list[CheckIssue]:
    """基本 Bash 语法模式匹配（ShellCheck 未安装时的降级）"""
    issues: list[CheckIssue] = []

    for i, line in enumerate(script.split("\n"), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # 简单检测：单/双引号数量为奇数
        if stripped.count("'") % 2 != 0 or stripped.count('"') % 2 != 0:
            issues.append(CheckIssue(
                category="syntax",
                severity=CheckSeverity.WARNING,
                message=f"第 {i} 行可能存在未闭合引号: {stripped[:60]}",
                command_index=-1,
            ))

    return issues


# ===== ② 环境兼容性检测 =====


def _check_env_compatibility(
    commands: list[CommandTemplate],
    script: str | None,
    env_type: EnvironmentType,
    *,
    has_docker_cli: bool = True,
    has_kubectl_cli: bool = True,
) -> list[CheckIssue]:
    """环境兼容性检测

    检测与环境不兼容的操作：
    - 容器环境：systemctl 可用（容器内服务由 systemd 管理，不报错）；
      kubectl/docker/crictl 在 CLI 不可用时 → WARNING（建议标记 requires_host）；
      modprobe/blkid/hwinfo 等需宿主机内核/块设备的操作 → WARNING（建议标记 requires_host）
    - VM 环境加载内核模块（modprobe）→ WARNING（确认虚拟化层兼容性）
    """
    issues: list[CheckIssue] = []
    all_commands = [cmd.command for cmd in commands]
    if script:
        all_commands.append(script)

    combined = "\n".join(all_commands)

    # 容器环境：仅对真正无法在容器内执行的命令提示（不阻断）
    if env_type == EnvironmentType.CONTAINER:
        # kubectl/crictl 在容器内 CLI 不可用时建议标记 requires_host（WARNING，不阻断）
        if not has_kubectl_cli:
            cli_advisory = [
                (r"\bkubectl\b", "kubectl 在容器内不可用（未挂载 kubeconfig）"),
                (r"\bcrictl\b", "crictl 在容器内不可用"),
            ]
            for pattern, message in cli_advisory:
                if re.search(pattern, combined):
                    issues.append(CheckIssue(
                        category="compatibility",
                        severity=CheckSeverity.WARNING,
                        message=f"容器环境提示: {message}，请将此类命令标记 requires_host 或使用替代命令",
                        command_index=-1,
                        suggestion="将命令设为 requires_host=true 由宿主机执行，或改用 systemctl/ps 等容器内命令",
                    ))
        if not has_docker_cli:
            if re.search(r"\bdocker\b", combined):
                issues.append(CheckIssue(
                    category="compatibility",
                    severity=CheckSeverity.WARNING,
                    message="容器环境提示: docker CLI 在容器内不可用（未挂载 docker.sock），请将此类命令标记 requires_host 或使用替代命令",
                    command_index=-1,
                    suggestion="将命令设为 requires_host=true 由宿主机执行，或改用 systemctl/journalctl 等容器内命令",
                ))
        # 需宿主机内核/块设备的操作：容器内确实无法执行，WARNING 建议标记 requires_host
        host_kernel_ops = [
            (r"\bmodprobe\b", "容器内无法加载内核模块，需在宿主机操作"),
            (r"\bblkid\b", "容器内无法直接访问块设备，需在宿主机操作"),
            (r"\bhwinfo\b", "容器内无法获取完整硬件信息，需在宿主机操作"),
        ]
        for pattern, message in host_kernel_ops:
            if re.search(pattern, combined):
                issues.append(CheckIssue(
                    category="compatibility",
                    severity=CheckSeverity.WARNING,
                    message=f"容器环境提示: {message}",
                    command_index=-1,
                    suggestion="将命令设为 requires_host=true 由宿主机执行",
                ))

    # VM 环境特定警告
    # VM 环境特定警告
    if env_type == EnvironmentType.VM:
        if re.search(r"\bmodprobe\b", combined):
            issues.append(CheckIssue(
                category="compatibility",
                severity=CheckSeverity.WARNING,
                message="VM 环境加载内核模块: 请确认模块与虚拟化层兼容",
                command_index=-1,
                suggestion="检查模块是否在 VM 环境中可用（如 vmw_pvscsi 需 VMware 环境）",
            ))

    return issues


# ===== ③ 危险模式建议性警告 =====


def _check_danger_advisory(
    commands: list[CommandTemplate],
    script: str | None,
) -> list[CheckIssue]:
    """危险模式建议性警告（D-03 性质：WARNING，不阻止）

    与 E-02 safety/danger.py 的关键差异：
    1. 使用通用代码质量维度（粒度较粗）
    2. 匹配命中 → WARNING（允许继续），而非 CRITICAL（强制拦截）
    3. 目的是信息展示和教育，而非安全拦截
    """
    issues: list[CheckIssue] = []

    for i, cmd in enumerate(commands):
        for pattern, description in _D03_DANGER_ADVISORY_PATTERNS:
            if re.search(pattern, cmd.command):
                issues.append(CheckIssue(
                    category="danger",
                    severity=CheckSeverity.WARNING,  # 始终 WARNING，不阻止
                    message=f"步骤 {i+1}: {description}",
                    command_index=i,
                    suggestion="EXECUTION_GUARD 阶段将做更深层安全检测",
                ))

    if script:
        for line_num, line in enumerate(script.split("\n"), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            for pattern, description in _D03_DANGER_ADVISORY_PATTERNS:
                if re.search(pattern, stripped):
                    issues.append(CheckIssue(
                        category="danger",
                        severity=CheckSeverity.WARNING,
                        message=f"脚本第 {line_num} 行: {description}",
                        command_index=-1,
                    ))

    return issues


# ===== 统一检测入口 =====


def check(
    commands: list[CommandTemplate],
    script: str | None,
    script_language: Literal["bash", "python"] | None,
    env_type: EnvironmentType,
    *,
    has_docker_cli: bool = True,
    has_kubectl_cli: bool = True,
) -> CheckResult:
    """D-03 生成后检测：代码质量保障

    检测维度：
    1. 语法检查（CRITICAL：阻止进入 REVIEWING）
    2. 环境兼容性检测（CRITICAL/WARNING：取决于环境 CLI 可用性）
    3. 危险模式建议性警告（WARNING：允许继续，但告知用户）

    注意：危险操作检测在此为建议性警告，非强制拦截。
    强制拦截由 safety/danger.py 在 EXECUTION_GUARD 步骤执行。
    """
    issues: list[CheckIssue] = []

    # ① 语法检查
    issues.extend(_check_unresolved_placeholders(commands, script))
    if script and script_language == "bash":
        issues.extend(_check_bash_syntax(script))

    # ② 环境兼容性检测
    issues.extend(_check_env_compatibility(
        commands, script, env_type,
        has_docker_cli=has_docker_cli,
        has_kubectl_cli=has_kubectl_cli,
    ))

    # ③ 危险模式建议性警告
    issues.extend(_check_danger_advisory(commands, script))

    return CheckResult(issues=issues)
