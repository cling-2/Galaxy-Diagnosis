"""持久化往返测试

验证含类型化 dataclass 与 collection_warnings 的 WorkflowState
序列化→反序列化后字段正确（防 persist 反序列化回归）。
"""

import io

from galaxy_diag.shared.types import (
    DiskInfo,
    EnvInfo,
    EnvironmentType,
    HardwareInfo,
    NicInfo,
    RaidCardInfo,
    StorageInfo,
    WorkflowState,
    WorkflowStep,
)
from galaxy_diag.workflow.persist import _state_to_dict, _dict_to_state


def make_state() -> WorkflowState:
    """构造含完整 EnvInfo 的 WorkflowState"""
    return WorkflowState(
        session_id="sess_test_001",
        current_step=WorkflowStep.DIAGNOSING,
        problem_description="数据磁盘未识别",
        env_info=EnvInfo(
            env_type=EnvironmentType.VM,
            hardware=HardwareInfo(
                cpu_model="Intel Xeon E5-2680 v4",
                cpu_cores=4,
                memory_total_gb=16.0,
                disks=[DiskInfo(type="SSD", capacity="100GB", model="sda")],
                raid_cards=[RaidCardInfo(model="MegaRAID", firmware_version="1.0")],
                nics=[NicInfo(model="virtio-net", driver="virtio_pci")],
            ),
            storage=[
                StorageInfo(
                    storage_type="NAS",
                    mount_path="/mnt/data",
                    filesystem="nfs4",
                    details={"server": "nas-01.internal"},
                ),
            ],
            collection_warnings=["容器环境无法采集宿主机硬件信息"],
            raw_output={"cpuinfo": "model name: Intel"},
        ),
    )


class TestPersistRoundtrip:
    def test_env_info_roundtrip(self):
        """EnvInfo 完整往返"""
        original = make_state()
        raw = _state_to_dict(original)
        restored = _dict_to_state(raw)

        assert restored.session_id == "sess_test_001"
        assert restored.env_info is not None
        assert restored.env_info.env_type == EnvironmentType.VM

    def test_disk_is_typed_dataclass(self):
        """恢复后 disks[0] 是 DiskInfo 而非 dict（属性访问正确）"""
        original = make_state()
        raw = _state_to_dict(original)
        restored = _dict_to_state(raw)

        disks = restored.env_info.hardware.disks
        assert len(disks) == 1
        # 属性访问（而非 dict.get）
        assert disks[0].model == "sda"
        assert disks[0].capacity == "100GB"
        assert disks[0].type == "SSD"
        assert isinstance(disks[0], DiskInfo)

    def test_raid_card_typed(self):
        original = make_state()
        raw = _state_to_dict(original)
        restored = _dict_to_state(raw)

        raid = restored.env_info.hardware.raid_cards
        assert len(raid) == 1
        assert raid[0].model == "MegaRAID"
        assert raid[0].firmware_version == "1.0"
        assert isinstance(raid[0], RaidCardInfo)

    def test_nic_typed(self):
        original = make_state()
        raw = _state_to_dict(original)
        restored = _dict_to_state(raw)

        nics = restored.env_info.hardware.nics
        assert len(nics) == 1
        assert nics[0].model == "virtio-net"
        assert nics[0].driver == "virtio_pci"
        assert isinstance(nics[0], NicInfo)

    def test_collection_warnings_preserved(self):
        original = make_state()
        raw = _state_to_dict(original)
        restored = _dict_to_state(raw)

        assert restored.env_info.collection_warnings == ["容器环境无法采集宿主机硬件信息"]

    def test_storage_details_preserved(self):
        original = make_state()
        raw = _state_to_dict(original)
        restored = _dict_to_state(raw)

        storage = restored.env_info.storage
        assert len(storage) == 1
        assert storage[0].storage_type == "NAS"
        assert storage[0].details.get("server") == "nas-01.internal"

    def test_raw_output_preserved(self):
        original = make_state()
        raw = _state_to_dict(original)
        restored = _dict_to_state(raw)

        assert restored.env_info.raw_output.get("cpuinfo") == "model name: Intel"

    def test_none_env_info_roundtrip(self):
        """env_info 为 None 时往返正确"""
        state = WorkflowState(
            session_id="sess_none",
            current_step=WorkflowStep.ENV_RECOGNISING,
        )
        raw = _state_to_dict(state)
        restored = _dict_to_state(raw)
        assert restored.env_info is None

    def test_forward_compat_unknown_keys(self):
        """旧会话文件含未知字段不报错"""
        original = make_state()
        raw = _state_to_dict(original)
        # 模拟旧/新版本混入的未知字段
        raw["env_info"]["hardware"]["disks"][0]["unknown_key"] = "x"
        restored = _dict_to_state(raw)
        assert restored.env_info.hardware.disks[0].model == "sda"
