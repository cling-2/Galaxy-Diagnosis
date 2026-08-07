"""collect_env 编排测试

覆盖顶层编排：识别 → 采集 → 组装 → warnings → raw_output。
"""

from unittest.mock import patch, MagicMock

import pytest

from galaxy_diag.collector import collect_env
from galaxy_diag.shared.types import (
    DiskInfo,
    EnvInfo,
    EnvironmentType,
    HardwareInfo,
    NicInfo,
    RaidCardInfo,
    StorageInfo,
)


class TestCollectEnv:
    def test_full_orchestration_bare_metal(self):
        """裸金属完整编排"""
        mock_hw = HardwareInfo(
            cpu_model="Intel Xeon",
            cpu_cores=8,
            memory_total_gb=32.0,
            disks=[DiskInfo(type="SSD", capacity="500GB", model="sda")],
            raid_cards=[RaidCardInfo(model="MegaRAID", firmware_version="1.0")],
            nics=[NicInfo(model="Intel I219", driver="e1000e")],
        )
        mock_storage = [
            StorageInfo(storage_type="local", mount_path="/", filesystem="ext4"),
        ]

        with patch("galaxy_diag.collector.EnvironmentDetector") as MockDetector, \
             patch("galaxy_diag.collector.HardwareCollector") as MockHW, \
             patch("galaxy_diag.collector.StorageCollector") as MockST:
            MockDetector.return_value.detect.return_value = EnvironmentType.BARE_METAL
            MockHW.return_value.collect.return_value = mock_hw
            MockHW.return_value.raw_output = {"cpuinfo": "..."}
            MockST.return_value.collect.return_value = mock_storage
            MockST.return_value.raw_output = {"findmnt": "..."}

            env_info = collect_env()

        assert isinstance(env_info, EnvInfo)
        assert env_info.env_type == EnvironmentType.BARE_METAL
        assert env_info.hardware.cpu_model == "Intel Xeon"
        assert len(env_info.storage) == 1
        assert env_info.raw_output  # 非空
        # 裸金属无容器警告
        assert not any("容器" in w for w in env_info.collection_warnings)

    def test_container_adds_host_hint(self):
        """容器环境追加宿主机提示"""
        with patch("galaxy_diag.collector.EnvironmentDetector") as MockDetector, \
             patch("galaxy_diag.collector.HardwareCollector") as MockHW, \
             patch("galaxy_diag.collector.StorageCollector") as MockST:
            MockDetector.return_value.detect.return_value = EnvironmentType.CONTAINER
            MockHW.return_value.collect.return_value = HardwareInfo()
            MockHW.return_value.raw_output = {}
            MockST.return_value.collect.return_value = []
            MockST.return_value.raw_output = {}

            env_info = collect_env()

        assert any("宿主机" in w for w in env_info.collection_warnings)

    def test_raw_output_truncation(self):
        """raw_output 长条目截断"""
        long_value = "x" * 3000
        with patch("galaxy_diag.collector.EnvironmentDetector") as MockDetector, \
             patch("galaxy_diag.collector.HardwareCollector") as MockHW, \
             patch("galaxy_diag.collector.StorageCollector") as MockST:
            MockDetector.return_value.detect.return_value = EnvironmentType.VM
            MockHW.return_value.collect.return_value = HardwareInfo()
            MockHW.return_value.raw_output = {"big_output": long_value}
            MockST.return_value.collect.return_value = []
            MockST.return_value.raw_output = {}

            env_info = collect_env()

        truncated = env_info.raw_output["big_output"]
        assert len(truncated) < 3000
        assert "[truncated]" in truncated

    def test_typed_dataclass_output(self):
        """输出含类型化 dataclass（DiskInfo 等）"""
        with patch("galaxy_diag.collector.EnvironmentDetector") as MockDetector, \
             patch("galaxy_diag.collector.HardwareCollector") as MockHW, \
             patch("galaxy_diag.collector.StorageCollector") as MockST:
            MockDetector.return_value.detect.return_value = EnvironmentType.VM
            MockHW.return_value.collect.return_value = HardwareInfo(
                disks=[DiskInfo(type="SSD", capacity="100GB", model="sda")],
                nics=[NicInfo(model="virtio-net", driver="virtio_pci")],
            )
            MockHW.return_value.raw_output = {}
            MockST.return_value.collect.return_value = []
            MockST.return_value.raw_output = {}

            env_info = collect_env()

        assert isinstance(env_info.hardware.disks[0], DiskInfo)
        assert isinstance(env_info.hardware.nics[0], NicInfo)
        assert env_info.hardware.disks[0].model == "sda"
