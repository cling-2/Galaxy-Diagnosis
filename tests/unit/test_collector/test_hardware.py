"""硬件采集测试（REQ-B-02）

覆盖 CPU/内存/磁盘/RAID/网卡解析、工具缺失降级、
容器跳过 RAID、部分失败不阻断。
"""

from unittest.mock import patch

import pytest

from galaxy_diag.collector.hardware import (
    HardwareCollector,
    _bytes_to_human,
    _parse_lsblk_line,
)
from galaxy_diag.shared.types import DiskInfo, EnvironmentType, RaidCardInfo


# ===== 工具函数 =====


class TestBytesToHuman:
    def test_gb(self):
        assert _bytes_to_human(500 * 1024 ** 3) == "500.0GB"

    def test_mb(self):
        assert _bytes_to_human(512 * 1024 ** 2) == "512MB"

    def test_zero(self):
        assert _bytes_to_human(0) == "0B"

    def test_invalid(self):
        assert _bytes_to_human("invalid") == ""


class TestParseLsblkLine:
    def test_parse(self):
        line = 'NAME="sda" TYPE="disk" SIZE="500107862016" MODEL="Samsung SSD"'
        parsed = _parse_lsblk_line(line)
        assert parsed["NAME"] == "sda"
        assert parsed["TYPE"] == "disk"
        assert parsed["SIZE"] == "500107862016"
        assert parsed["MODEL"] == "Samsung SSD"

    def test_empty(self):
        assert _parse_lsblk_line("") is None

    def test_no_match(self):
        assert _parse_lsblk_line("no quoted fields here") is None


# ===== CPU 采集 =====


class TestCpuCollection:
    def test_cpu_model_and_cores(self):
        cpuinfo = (
            "processor\t: 0\n"
            "model name\t: Intel(R) Xeon(R) CPU E5-2680 v4 @ 2.40GHz\n"
            "processor\t: 1\n"
            "model name\t: Intel(R) Xeon(R) CPU E5-2680 v4 @ 2.40GHz\n"
            "processor\t: 2\n"
            "model name\t: Intel(R) Xeon(R) CPU E5-2680 v4 @ 2.40GHz\n"
            "processor\t: 3\n"
            "model name\t: Intel(R) Xeon(R) CPU E5-2680 v4 @ 2.40GHz\n"
        )
        collector = HardwareCollector()
        with patch("galaxy_diag.collector.hardware._read_file", return_value=cpuinfo):
            model, cores = collector._collect_cpu()
        assert "Intel(R) Xeon(R)" in model
        assert cores == 4

    def test_cpu_file_missing(self):
        collector = HardwareCollector()
        with patch("galaxy_diag.collector.hardware._read_file", return_value=None):
            model, cores = collector._collect_cpu()
        assert model == ""
        assert cores == 0


# ===== 内存采集 =====


class TestMemoryCollection:
    def test_memory_total(self):
        meminfo = (
            "MemTotal:       16777216 kB\n"
            "MemFree:         8000000 kB\n"
            "MemAvailable:    9000000 kB\n"
        )
        collector = HardwareCollector()
        with patch("galaxy_diag.collector.hardware._read_file", return_value=meminfo):
            mem = collector._collect_memory()
        assert mem == 16.0

    def test_memory_missing_field(self):
        meminfo = "MemFree: 100 kB\n"
        collector = HardwareCollector()
        with patch("galaxy_diag.collector.hardware._read_file", return_value=meminfo):
            mem = collector._collect_memory()
        assert mem == 0.0


# ===== 磁盘采集 =====


class TestDiskCollection:
    def test_disks_parsed(self):
        lsblk_output = (
            'NAME="sda" TYPE="disk" SIZE="500107862016" MODEL="Samsung SSD"\n'
            'NAME="sda1" TYPE="part" SIZE="500107862016" MODEL=""\n'
            'NAME="sdb" TYPE="disk" SIZE="1000204886016" MODEL="WD HDD"\n'
            'NAME="sr0" TYPE="rom" SIZE="1073741312" MODEL=""\n'
        )
        collector = HardwareCollector()
        with patch("galaxy_diag.collector.hardware._run_cmd", return_value=lsblk_output):
            warnings = []
            disks = collector._collect_disks(warnings)
        assert len(disks) == 2  # 跳过 part 和 rom
        assert isinstance(disks[0], DiskInfo)
        assert "Samsung SSD" in disks[0].model
        assert "GB" in disks[0].capacity
        assert "WD HDD" in disks[1].model

    def test_lsblk_missing(self):
        collector = HardwareCollector()
        with patch("galaxy_diag.collector.hardware._run_cmd", return_value=None):
            warnings = []
            disks = collector._collect_disks(warnings)
        assert disks == []
        assert any("lsblk" in w for w in warnings)


# ===== RAID 采集 =====


class TestRaidCollection:
    def test_storcli_available(self):
        storcli_output = (
            "Controller = 0\n"
            "Status = Success\n"
            "Product Name = Broadcom MegaRAID 9560-8i\n"
            "Firmware Version = 4.12.13-1234\n"
        )
        collector = HardwareCollector()
        with patch(
            "galaxy_diag.collector.hardware._run_cmd",
            return_value=storcli_output,
        ):
            cards = collector._collect_raid([])
        assert len(cards) == 1
        assert isinstance(cards[0], RaidCardInfo)

    def test_all_raid_tools_missing(self):
        collector = HardwareCollector()
        with patch("galaxy_diag.collector.hardware._run_cmd", return_value=None):
            warnings = []
            cards = collector._collect_raid(warnings)
        assert cards == []
        assert any("RAID" in w for w in warnings)


# ===== 网卡采集 =====


class TestNicCollection:
    def test_nics_from_lspci(self):
        lspci_output = (
            "00:1f.6 Ethernet controller: Intel Corporation I219-LM (rev 10)\n"
            "01:00.0 Network controller: Broadcom BCM4360 (rev 03)\n"
        )
        collector = HardwareCollector()
        with patch("galaxy_diag.collector.hardware._run_cmd", return_value=lspci_output), \
             patch("galaxy_diag.collector.hardware._lookup_nic_driver", return_value="e1000e"):
            nics = collector._collect_nics([])
        assert len(nics) == 2
        assert "Intel" in nics[0].model
        assert nics[0].driver == "e1000e"

    def test_lspci_missing_fallback_interfaces(self):
        collector = HardwareCollector()
        with patch("galaxy_diag.collector.hardware._run_cmd", return_value=None), \
             patch("galaxy_diag.collector.hardware.HardwareCollector._enumerate_net_interfaces",
                   return_value=["eth0", "eth1"]), \
             patch("galaxy_diag.collector.hardware._read_sysfs_driver", return_value="ixgbe"):
            warnings = []
            nics = collector._collect_nics(warnings)
        assert len(nics) == 2
        assert any("lspci" in w for w in warnings)
        assert nics[0].driver == "ixgbe"


# ===== 容器环境策略 =====


class TestContainerStrategy:
    def test_container_skips_raid(self):
        """容器环境跳过 RAID 采集，追加宿主机提示"""
        collector = HardwareCollector()
        with patch.object(collector, "_collect_cpu", return_value=("CPU", 2)), \
             patch.object(collector, "_collect_memory", return_value=8.0), \
             patch.object(collector, "_collect_disks", return_value=[]), \
             patch.object(collector, "_collect_nics", return_value=[]), \
             patch.object(collector, "_collect_raid") as mock_raid:
            warnings = []
            collector.collect(EnvironmentType.CONTAINER, warnings)
        mock_raid.assert_not_called()
        assert any("容器环境" in w for w in warnings)

    def test_bare_metal_collects_raid(self):
        """裸金属环境正常采集 RAID"""
        collector = HardwareCollector()
        with patch.object(collector, "_collect_cpu", return_value=("CPU", 2)), \
             patch.object(collector, "_collect_memory", return_value=8.0), \
             patch.object(collector, "_collect_disks", return_value=[]), \
             patch.object(collector, "_collect_nics", return_value=[]), \
             patch.object(collector, "_collect_raid", return_value=[]):
            warnings = []
            collector.collect(EnvironmentType.BARE_METAL, warnings)
        # 裸金属不应追加容器提示
        assert not any("容器环境" in w for w in warnings)

    def test_partial_failure_continues(self):
        """RAID 失败但 CPU/MEM 成功 → 仍返回 HardwareInfo，不抛异常"""
        collector = HardwareCollector()
        with patch.object(collector, "_collect_cpu", return_value=("CPU", 2)), \
             patch.object(collector, "_collect_memory", return_value=8.0), \
             patch.object(collector, "_collect_disks", return_value=[]), \
             patch.object(collector, "_collect_nics", return_value=[]), \
             patch.object(collector, "_collect_raid", return_value=[]):
            warnings = []
            hw = collector.collect(EnvironmentType.BARE_METAL, warnings)
        assert hw.cpu_model == "CPU"
        assert hw.cpu_cores == 2
        assert hw.memory_total_gb == 8.0
