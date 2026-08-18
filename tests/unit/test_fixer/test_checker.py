"""D-03 生成后检测器测试（fixer.checker）

验证环境兼容性检测 + 语法检测 + 危险模式警告的严重性判定。
核心回归：容器内 systemctl 不再被判 CRITICAL（Cause B 修复后）。
"""

import pytest

from galaxy_diag.fixer.checker import check
from galaxy_diag.shared.types import (
    CheckSeverity,
    CommandTemplate,
    EnvironmentType,
)


def _cmd(command: str, **kw) -> CommandTemplate:
    """快捷构造 CommandTemplate"""
    return CommandTemplate(command=command, **kw)


# ===== 容器环境：systemctl 不再 CRITICAL =====


class TestContainerSystemctl:
    def test_container_systemctl_not_critical(self):
        """容器内 systemctl 不应触发 CRITICAL（容器可由 systemd 管理）"""
        result = check(
            commands=[_cmd("systemctl restart galaxy-network")],
            script=None,
            script_language=None,
            env_type=EnvironmentType.CONTAINER,
            has_docker_cli=False,
            has_kubectl_cli=False,
        )
        assert result.passed is True
        assert not result.has_critical

    def test_container_systemctl_status_not_critical(self):
        """容器内 systemctl status 也不应 CRITICAL"""
        result = check(
            commands=[_cmd("systemctl status galaxy-api --no-pager")],
            script=None,
            script_language=None,
            env_type=EnvironmentType.CONTAINER,
            has_docker_cli=False,
            has_kubectl_cli=False,
        )
        assert result.passed is True


# ===== 容器环境：kubectl/crictl 按 CLI 可用性 WARNING =====


class TestContainerKubectl:
    def test_container_kubectl_without_cli_warns(self):
        """容器内 kubectl 在 CLI 不可用时应 WARNING"""
        result = check(
            commands=[_cmd("kubectl get pods -n kube-system")],
            script=None,
            script_language=None,
            env_type=EnvironmentType.CONTAINER,
            has_docker_cli=False,
            has_kubectl_cli=False,
        )
        assert result.passed is True  # WARNING 不阻断
        assert result.has_warning
        compat_warnings = [
            i for i in result.issues
            if i.category == "compatibility" and "kubectl" in i.message
        ]
        assert len(compat_warnings) >= 1

    def test_container_kubectl_with_cli_no_warn(self):
        """容器内 kubectl 在 CLI 可用时不报 WARNING"""
        result = check(
            commands=[_cmd("kubectl get pods -n kube-system")],
            script=None,
            script_language=None,
            env_type=EnvironmentType.CONTAINER,
            has_docker_cli=True,
            has_kubectl_cli=True,
        )
        # kubectl 可用时不报兼容性问题
        compat_issues = [
            i for i in result.issues if i.category == "compatibility"
        ]
        assert len(compat_issues) == 0

    def test_container_crictl_without_cli_warns(self):
        """容器内 crictl 在 CLI 不可用时应 WARNING"""
        result = check(
            commands=[_cmd("crictl ps")],
            script=None,
            script_language=None,
            env_type=EnvironmentType.CONTAINER,
            has_docker_cli=False,
            has_kubectl_cli=False,
        )
        assert result.passed is True
        assert result.has_warning


# ===== 容器环境：docker 按 CLI 可用性 WARNING =====


class TestContainerDocker:
    def test_container_docker_without_cli_warns(self):
        """容器内 docker 在 CLI 不可用时应 WARNING"""
        result = check(
            commands=[_cmd("docker ps")],
            script=None,
            script_language=None,
            env_type=EnvironmentType.CONTAINER,
            has_docker_cli=False,
            has_kubectl_cli=False,
        )
        assert result.passed is True
        assert result.has_warning
        compat_warnings = [
            i for i in result.issues
            if i.category == "compatibility" and "docker" in i.message
        ]
        assert len(compat_warnings) >= 1

    def test_container_docker_with_cli_no_warn(self):
        """容器内 docker 在 CLI 可用时不报 WARNING"""
        result = check(
            commands=[_cmd("docker ps")],
            script=None,
            script_language=None,
            env_type=EnvironmentType.CONTAINER,
            has_docker_cli=True,
            has_kubectl_cli=True,
        )
        compat_issues = [
            i for i in result.issues if i.category == "compatibility"
        ]
        assert len(compat_issues) == 0


# ===== 容器环境：需宿主机内核操作 → WARNING + requires_host 建议 =====


class TestContainerHostKernelOps:
    def test_container_modprobe_warns_requires_host(self):
        """容器内 modprobe → WARNING，建议 requires_host"""
        result = check(
            commands=[_cmd("modprobe vmw_pvscsi")],
            script=None,
            script_language=None,
            env_type=EnvironmentType.CONTAINER,
            has_docker_cli=False,
            has_kubectl_cli=False,
        )
        assert result.passed is True
        assert result.has_warning
        compat = [i for i in result.issues if i.category == "compatibility"]
        assert any("requires_host" in (c.suggestion or "") for c in compat)

    def test_container_blkid_warns(self):
        """容器内 blkid → WARNING"""
        result = check(
            commands=[_cmd("blkid")],
            script=None,
            script_language=None,
            env_type=EnvironmentType.CONTAINER,
        )
        assert result.passed is True
        assert result.has_warning

    def test_container_hwinfo_warns(self):
        """容器内 hwinfo → WARNING"""
        result = check(
            commands=[_cmd("hwinfo --disk")],
            script=None,
            script_language=None,
            env_type=EnvironmentType.CONTAINER,
        )
        assert result.passed is True
        assert result.has_warning


# ===== 语法检测：占位符仍应 CRITICAL =====


class TestPlaceholderCritical:
    def test_placeholder_is_critical(self):
        """未替换占位符仍应为 CRITICAL（回归保护）"""
        result = check(
            commands=[_cmd("systemctl restart <SERVICE_NAME>", editable_params={"SERVICE_NAME": "galaxy"})],
            script=None,
            script_language=None,
            env_type=EnvironmentType.CONTAINER,
        )
        assert result.has_critical
        assert result.passed is False

    def test_placeholder_in_script_is_critical(self):
        """脚本中未替换占位符也应 CRITICAL"""
        result = check(
            commands=[_cmd("echo ok")],
            script="#!/bin/bash\nsystemctl restart <FOO>",
            script_language="bash",
            env_type=EnvironmentType.VM,
        )
        assert result.has_critical
        assert result.passed is False


# ===== VM 环境 =====


class TestVMEnvironment:
    def test_vm_modprobe_warns(self):
        """VM 环境中 modprobe → WARNING（确认虚拟化层兼容性）"""
        result = check(
            commands=[_cmd("modprobe vmw_pvscsi")],
            script=None,
            script_language=None,
            env_type=EnvironmentType.VM,
        )
        assert result.passed is True
        assert result.has_warning

    def test_vm_systemctl_ok(self):
        """VM 环境中 systemctl 完全没问题"""
        result = check(
            commands=[_cmd("systemctl restart galaxy-network")],
            script=None,
            script_language=None,
            env_type=EnvironmentType.VM,
        )
        assert result.passed is True
        assert not result.has_critical


# ===== 危险模式建议性警告 =====


class TestDangerAdvisory:
    def test_systemctl_restart_is_warning(self):
        """systemctl restart 在危险模式中为 WARNING（不阻断）"""
        result = check(
            commands=[_cmd("systemctl restart galaxy-api")],
            script=None,
            script_language=None,
            env_type=EnvironmentType.VM,
        )
        # danger advisory 是 WARNING，不应导致 has_critical
        assert result.passed is True
        danger_issues = [i for i in result.issues if i.category == "danger"]
        assert len(danger_issues) >= 1
        assert all(i.severity == CheckSeverity.WARNING for i in danger_issues)
