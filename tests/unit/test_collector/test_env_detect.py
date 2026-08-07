"""环境识别测试（REQ-B-01）

覆盖 ContainerDetector / VMDetector / BareMetalDetector /
EnvironmentDetector 优先级链与降级逻辑。
"""

from unittest.mock import patch

import pytest

from galaxy_diag.collector.env_detect import (
    BareMetalDetector,
    ContainerDetector,
    EnvironmentDetector,
    VMDetector,
)
from galaxy_diag.shared.types import EnvironmentType


# ===== ContainerDetector =====


class TestContainerDetector:
    """容器环境检测"""

    def test_dockerenv_exists(self):
        """信号 1: /.dockerenv 存在 → CONTAINER"""
        with patch("galaxy_diag.collector.env_detect.os.path.exists") as mock_exists:
            mock_exists.return_value = True
            result = ContainerDetector().detect(warnings=[])
            assert result == EnvironmentType.CONTAINER

    def test_dockerenv_not_exists_no_cgroup(self):
        """/.dockerenv 不存在且无 cgroup → None"""
        with patch("galaxy_diag.collector.env_detect.os.path.exists") as mock_exists, \
             patch("galaxy_diag.collector.env_detect._read_file") as mock_read:
            mock_exists.return_value = False
            mock_read.return_value = None
            result = ContainerDetector().detect(warnings=[])
            assert result is None

    def test_cgroup_docker(self):
        """信号 2: /proc/1/cgroup 含 docker → CONTAINER"""
        with patch("galaxy_diag.collector.env_detect.os.path.exists") as mock_exists, \
             patch("galaxy_diag.collector.env_detect._read_file") as mock_read:
            mock_exists.return_value = False
            # 第一次调用读 cgroup，第二次读 mountinfo
            mock_read.side_effect = [
                "12:memory:/docker/abc123\n11:cpuset:/docker/abc123",
                None,
            ]
            result = ContainerDetector().detect(warnings=[])
            assert result == EnvironmentType.CONTAINER

    def test_cgroup_kubepods(self):
        """信号 2: /proc/1/cgroup 含 kubepods → CONTAINER"""
        with patch("galaxy_diag.collector.env_detect.os.path.exists") as mock_exists, \
             patch("galaxy_diag.collector.env_detect._read_file") as mock_read:
            mock_exists.return_value = False
            mock_read.side_effect = [
                "12:memory:/kubepods/besteffort/pod123\n",
                None,
            ]
            result = ContainerDetector().detect(warnings=[])
            assert result == EnvironmentType.CONTAINER

    def test_cgroup_no_container_keywords(self):
        """信号 2: /proc/1/cgroup 不含容器关键词 → None（继续检查 mountinfo）"""
        with patch("galaxy_diag.collector.env_detect.os.path.exists") as mock_exists, \
             patch("galaxy_diag.collector.env_detect._read_file") as mock_read:
            mock_exists.return_value = False
            mock_read.side_effect = [
                "12:memory:/user.slice\n",
                None,
            ]
            result = ContainerDetector().detect(warnings=[])
            assert result is None

    def test_mountinfo_overlay(self):
        """信号 3: /proc/self/mountinfo 含 overlay 挂载在根 → CONTAINER"""
        mountinfo = (
            "36 31 0:35 / /sys rw,nosuid,nodev,noexec - sysfs sysfs rw\n"
            "37 31 0:36 / / ro - overlay overlay rw,lowerdir=...\n"
        )
        with patch("galaxy_diag.collector.env_detect.os.path.exists") as mock_exists, \
             patch("galaxy_diag.collector.env_detect._read_file") as mock_read:
            mock_exists.return_value = False
            mock_read.side_effect = [
                "12:memory:/user.slice\n",  # cgroup 不含容器关键词
                mountinfo,
            ]
            result = ContainerDetector().detect(warnings=[])
            assert result == EnvironmentType.CONTAINER


# ===== VMDetector =====


class TestVMDetector:
    """虚拟机环境检测"""

    def test_systemd_detect_virt_kvm(self):
        """信号 1: systemd-detect-virt 返回 kvm → VM"""
        with patch("galaxy_diag.collector.env_detect._run_cmd") as mock_run, \
             patch("galaxy_diag.collector.env_detect._read_file") as mock_read:
            mock_run.return_value = "kvm"
            mock_read.return_value = None
            result = VMDetector().detect(warnings=[])
            assert result == EnvironmentType.VM

    def test_systemd_detect_virt_vmware(self):
        """信号 1: systemd-detect-virt 返回 vmware → VM"""
        with patch("galaxy_diag.collector.env_detect._run_cmd") as mock_run, \
             patch("galaxy_diag.collector.env_detect._read_file") as mock_read:
            mock_run.return_value = "vmware"
            mock_read.return_value = None
            result = VMDetector().detect(warnings=[])
            assert result == EnvironmentType.VM

    def test_systemd_detect_virt_none(self):
        """信号 1: systemd-detect-virt 返回 none → 继续检查 DMI"""
        with patch("galaxy_diag.collector.env_detect._run_cmd") as mock_run, \
             patch("galaxy_diag.collector.env_detect._read_file") as mock_read:
            mock_run.return_value = "none"
            mock_read.return_value = None
            result = VMDetector().detect(warnings=[])
            assert result is None

    def test_dmi_product_name_vmware(self):
        """信号 2: DMI product_name 含 VMware → VM"""
        with patch("galaxy_diag.collector.env_detect._run_cmd") as mock_run, \
             patch("galaxy_diag.collector.env_detect._read_file") as mock_read:
            mock_run.return_value = None  # systemd-detect-virt 不可用
            mock_read.return_value = "VMware Virtual Platform"
            result = VMDetector().detect(warnings=[])
            assert result == EnvironmentType.VM

    def test_dmi_product_name_kvm(self):
        """信号 2: DMI product_name 含 KVM → VM"""
        with patch("galaxy_diag.collector.env_detect._run_cmd") as mock_run, \
             patch("galaxy_diag.collector.env_detect._read_file") as mock_read:
            mock_run.return_value = None
            mock_read.return_value = "KVM Virtual Machine"
            result = VMDetector().detect(warnings=[])
            assert result == EnvironmentType.VM

    def test_all_signals_missing(self):
        """所有 VM 信号均不可用 → None"""
        with patch("galaxy_diag.collector.env_detect._run_cmd") as mock_run, \
             patch("galaxy_diag.collector.env_detect._read_file") as mock_read, \
             patch("galaxy_diag.collector.env_detect.os.path.isdir") as mock_isdir:
            mock_run.return_value = None
            mock_read.return_value = None
            mock_isdir.return_value = False
            result = VMDetector().detect(warnings=[])
            assert result is None

    def test_scsi_vendor_qemu(self):
        """信号 3: SCSI vendor 含 QEMU → VM"""
        with patch("galaxy_diag.collector.env_detect._run_cmd") as mock_run, \
             patch("galaxy_diag.collector.env_detect._read_file") as mock_read, \
             patch("galaxy_diag.collector.env_detect.os.path.isdir") as mock_isdir, \
             patch("galaxy_diag.collector.env_detect.os.listdir") as mock_listdir:
            mock_run.return_value = None
            # DMI 不可读 → None，SCSI vendor → QEMU
            mock_read.side_effect = [None, "QEMU"]
            mock_isdir.return_value = True
            mock_listdir.return_value = ["0:0:0:0"]
            result = VMDetector().detect(warnings=[])
            assert result == EnvironmentType.VM

    def test_scsi_permission_error(self):
        """SCSI 读取权限不足 → 记录 warning，返回 None"""
        with patch("galaxy_diag.collector.env_detect._run_cmd") as mock_run, \
             patch("galaxy_diag.collector.env_detect._read_file") as mock_read, \
             patch("galaxy_diag.collector.env_detect.os.path.isdir") as mock_isdir, \
             patch("galaxy_diag.collector.env_detect.os.listdir") as mock_listdir:
            mock_run.return_value = None
            mock_read.return_value = None
            mock_isdir.return_value = True
            mock_listdir.side_effect = PermissionError("no access")
            warnings = []
            result = VMDetector().detect(warnings)
            assert result is None
            assert any("权限" in w for w in warnings)


# ===== BareMetalDetector =====


class TestBareMetalDetector:
    """裸金属环境（兜底）"""

    def test_always_returns_bare_metal(self):
        result = BareMetalDetector().detect(warnings=[])
        assert result == EnvironmentType.BARE_METAL


# ===== EnvironmentDetector 优先级 =====


class TestEnvironmentDetectorPriority:
    """环境检测优先级链"""

    def test_container_wins_over_vm(self):
        """容器信号 + VM 信号同时存在 → CONTAINER 优先"""
        detector = EnvironmentDetector()
        # ContainerDetector 命中
        with patch.object(
            detector._detectors[0], "detect", return_value=EnvironmentType.CONTAINER
        ):
            warnings = []
            result = detector.detect(warnings)
            assert result == EnvironmentType.CONTAINER

    def test_vm_when_no_container(self):
        """仅 VM 信号 → VM"""
        detector = EnvironmentDetector()
        with patch.object(detector._detectors[0], "detect", return_value=None), \
             patch.object(detector._detectors[1], "detect", return_value=EnvironmentType.VM):
            result = detector.detect(warnings=[])
            assert result == EnvironmentType.VM

    def test_bare_metal_fallback(self):
        """无信号 → BARE_METAL"""
        detector = EnvironmentDetector()
        with patch.object(detector._detectors[0], "detect", return_value=None), \
             patch.object(detector._detectors[1], "detect", return_value=None):
            result = detector.detect(warnings=[])
            assert result == EnvironmentType.BARE_METAL

    def test_detect_creates_warnings_if_none(self):
        """warnings 为 None 时自动创建"""
        detector = EnvironmentDetector()
        with patch.object(detector._detectors[0], "detect", return_value=EnvironmentType.CONTAINER):
            result = detector.detect(warnings=None)
            assert result == EnvironmentType.CONTAINER


# ===== 降级测试 =====


class TestDegradation:
    """降级方案"""

    def test_no_systemd_no_dmi_fallback_to_bare_metal(self):
        """systemd-detect-virt 不可用 + DMI 不可读 + SCSI 不可读 → BARE_METAL"""
        detector = EnvironmentDetector()
        with patch.object(detector._detectors[0], "detect", return_value=None), \
             patch("galaxy_diag.collector.env_detect._run_cmd", return_value=None), \
             patch("galaxy_diag.collector.env_detect._read_file", return_value=None), \
             patch("galaxy_diag.collector.env_detect.os.path.isdir", return_value=False):
            warnings = []
            result = detector.detect(warnings)
            assert result == EnvironmentType.BARE_METAL
