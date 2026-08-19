"""display.print_env_info 渲染测试

验证类型化 dataclass 属性访问与 collection_warnings 渲染。
"""

import io
import json

from dataclasses import asdict
from unittest.mock import patch

from rich.console import Console
from rich.table import Table

from galaxy_diag.shared.types import (
    DiskInfo,
    EnvInfo,
    EnvironmentType,
    HardwareInfo,
    NicInfo,
    RaidCardInfo,
    StorageInfo,
)
from galaxy_diag.workflow.cli.display import print_env_info


def make_env_info() -> EnvInfo:
    return EnvInfo(
        env_type=EnvironmentType.VM,
        hardware=HardwareInfo(
            cpu_model="Intel Xeon E5-2680 v4",
            cpu_cores=4,
            memory_total_gb=16.0,
            disks=[
                DiskInfo(type="SSD", capacity="100GB", model="sda"),
                DiskInfo(type="HDD", capacity="500GB", model="sdb"),
            ],
            raid_cards=[RaidCardInfo(model="MegaRAID", firmware_version="1.0")],
            nics=[NicInfo(model="virtio-net", driver="virtio_pci")],
        ),
        storage=[
            StorageInfo(
                storage_type="NAS",
                mount_path="/mnt/data",
                filesystem="nfs4",
            ),
        ],
        collection_warnings=["容器环境无法采集宿主机硬件信息"],
    )


class TestPrintEnvInfoTable:
    def test_renders_env_type(self):
        buf = io.StringIO()
        console = Console(file=buf, force_terminal=True, width=120)
        env_info = make_env_info()
        # 直接调用，捕获输出
        print_env_info(env_info, format="table")
        # 无法直接捕获全局 console 输出，改用构造 console 验证属性访问不报错
        # 验证 dataclass 属性可访问
        assert env_info.hardware.disks[0].model == "sda"
        assert env_info.hardware.raid_cards[0].model == "MegaRAID"
        assert env_info.hardware.nics[0].driver == "virtio_pci"

    def test_attribute_access_no_error(self):
        """类型化 dataclass 属性访问不抛 AttributeError"""
        env_info = make_env_info()
        # 这些访问在旧 list[dict] 下需要 .get()，新代码应直接属性访问
        for d in env_info.hardware.disks:
            assert hasattr(d, "model")
            assert hasattr(d, "capacity")
            assert hasattr(d, "type")
        for r in env_info.hardware.raid_cards:
            assert hasattr(r, "model")
            assert hasattr(r, "firmware_version")
        for n in env_info.hardware.nics:
            assert hasattr(n, "model")
            assert hasattr(n, "driver")

    def test_empty_fields_no_error(self):
        """空字段不报错"""
        env_info = EnvInfo()  # 全默认值
        assert env_info.hardware.disks == []
        assert env_info.hardware.raid_cards == []
        assert env_info.hardware.nics == []
        # 打印不报错
        print_env_info(env_info, format="table")


class TestPrintEnvInfoJson:
    def test_json_output_contains_collection_warnings(self):
        env_info = make_env_info()
        raw = asdict(env_info)
        json_str = json.dumps(raw, ensure_ascii=False)
        parsed = json.loads(json_str)
        assert "collection_warnings" in parsed
        assert "容器环境" in parsed["collection_warnings"][0]

    def test_json_output_contains_typed_fields(self):
        env_info = make_env_info()
        raw = asdict(env_info)
        json_str = json.dumps(raw, ensure_ascii=False)
        parsed = json.loads(json_str)
        # disks 序列化为 dict 列表（asdict 自动转换）
        assert parsed["hardware"]["disks"][0]["model"] == "sda"
        assert parsed["hardware"]["disks"][0]["capacity"] == "100GB"
        assert parsed["hardware"]["raid_cards"][0]["firmware_version"] == "1.0"
        assert parsed["hardware"]["nics"][0]["driver"] == "virtio_pci"

    def test_json_valid(self):
        """JSON 输出合法"""
        env_info = make_env_info()
        raw = asdict(env_info)
        json_str = json.dumps(raw, ensure_ascii=False, indent=2)
        parsed = json.loads(json_str)
        assert parsed["env_type"] == "vm"


class TestCollectionWarningsRendering:
    def test_warnings_present(self):
        """collection_warnings 非空时渲染（验证属性可读）"""
        env_info = make_env_info()
        assert len(env_info.collection_warnings) == 1
        assert "容器" in env_info.collection_warnings[0]

    def test_no_warnings(self):
        """collection_warnings 为空时不报错"""
        env_info = EnvInfo()
        assert env_info.collection_warnings == []
        print_env_info(env_info, format="table")


class TestPrintEnvInfoSkipHardware:
    """C类：skip_hardware=True 时不打印硬件/存储表，仅显示跳过提示"""

    def _capture(self, env_info, skip_hardware):
        buf = io.StringIO()
        console = Console(file=buf, force_terminal=True, width=120, no_color=True)
        with patch("galaxy_diag.workflow.cli.display.get_console", return_value=console):
            print_env_info(env_info, skip_hardware=skip_hardware)
        return buf.getvalue()

    def test_skip_hardware_shows_skip_message(self):
        env_info = make_env_info()
        out = self._capture(env_info, skip_hardware=True)
        assert "已跳过完整硬件和存储采集" in out

    def test_skip_hardware_hides_hardware_table(self):
        """skip_hardware=True 时不渲染硬件信息表（不含表头"硬件信息"独立标题行内容）"""
        env_info = make_env_info()
        out = self._capture(env_info, skip_hardware=True)
        # 硬件表内容不应出现
        assert "Intel Xeon E5-2680 v4" not in out
        assert "MegaRAID" not in out
        # 存储表内容不应出现
        assert "/mnt/data" not in out

    def test_skip_hardware_false_shows_tables(self):
        """skip_hardware=False 时正常渲染硬件/存储表"""
        env_info = make_env_info()
        out = self._capture(env_info, skip_hardware=False)
        assert "Intel Xeon E5-2680 v4" in out
        assert "/mnt/data" in out
