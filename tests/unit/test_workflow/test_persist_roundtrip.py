"""持久化往返测试

验证含类型化 dataclass 与 collection_warnings 的 WorkflowState
序列化→反序列化后字段正确（防 persist 反序列化回归）。
"""

import io

from galaxy_diag.shared.types import (
    ContainerRuntime,
    DiagnosticContext,
    DiskInfo,
    EnvInfo,
    EnvironmentType,
    HardwareInfo,
    LogSnippet,
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


# ===== DiagnosticContext 往返 =====


def make_state_with_ctx() -> WorkflowState:
    """构造含 DiagnosticContext 的 WorkflowState"""
    return WorkflowState(
        session_id="sess_ctx_001",
        current_step=WorkflowStep.DIAGNOSING,
        problem_description="磁盘挂载失败",
        env_info=EnvInfo(
            env_type=EnvironmentType.CONTAINER,
            container_runtime=ContainerRuntime.KUBERNETES,
            hardware=HardwareInfo(),
        ),
        diagnostic_context=DiagnosticContext(
            problem_description="磁盘挂载失败",
            env_info_ref=EnvironmentType.CONTAINER,
            container_runtime=ContainerRuntime.KUBERNETES,
            component_status=[
                {"name": "galaxy-storage", "status": "failed", "detail": "kubectl: CrashLoopBackOff"},
                {"name": "galaxy-compute", "status": "running", "detail": "kubectl: Running"},
            ],
            log_snippets=[
                LogSnippet(
                    source="kubectl:logs",
                    level="ERROR",
                    timestamp="",
                    content="disk mount failed: /dev/sdb not found",
                    truncated=False,
                ),
            ],
            system_resources={"load_avg": "0.5 0.4 0.3", "mem_total_gb": 16.0},
            network_checks=[{"target": "iptables", "reachable": True, "detail": "-P INPUT ACCEPT"}],
            user_provided=["[user-upload:/tmp/dmesg.log]\nATA error"],
            collection_warnings=["kubectl logs 部分容器无日志"],
            raw_output={"component_status": "galaxy-storage: failed"},
            collected_tools=["collect_component_status", "collect_service_logs", "collect_system_resources"],
        ),
    )


class TestDiagnosticContextRoundtrip:
    def test_diagnostic_context_roundtrip(self):
        """DiagnosticContext 完整往返"""
        original = make_state_with_ctx()
        raw = _state_to_dict(original)
        restored = _dict_to_state(raw)

        assert restored.diagnostic_context is not None
        ctx = restored.diagnostic_context
        assert ctx.problem_description == "磁盘挂载失败"
        assert ctx.env_info_ref == EnvironmentType.CONTAINER
        assert ctx.container_runtime == ContainerRuntime.KUBERNETES

    def test_log_snippets_typed(self):
        """恢复后 log_snippets 是 LogSnippet 而非 dict"""
        original = make_state_with_ctx()
        raw = _state_to_dict(original)
        restored = _dict_to_state(raw)

        snippets = restored.diagnostic_context.log_snippets
        assert len(snippets) == 1
        assert isinstance(snippets[0], LogSnippet)
        assert snippets[0].source == "kubectl:logs"
        assert snippets[0].level == "ERROR"
        assert "disk mount failed" in snippets[0].content
        assert snippets[0].truncated is False

    def test_component_status_preserved(self):
        """component_status 列表内容保留"""
        original = make_state_with_ctx()
        raw = _state_to_dict(original)
        restored = _dict_to_state(raw)

        cs = restored.diagnostic_context.component_status
        assert len(cs) == 2
        assert cs[0]["name"] == "galaxy-storage"
        assert cs[0]["status"] == "failed"
        assert cs[1]["status"] == "running"

    def test_system_resources_preserved(self):
        """system_resources dict 保留"""
        original = make_state_with_ctx()
        raw = _state_to_dict(original)
        restored = _dict_to_state(raw)

        sr = restored.diagnostic_context.system_resources
        assert sr["load_avg"] == "0.5 0.4 0.3"
        assert sr["mem_total_gb"] == 16.0

    def test_collected_tools_preserved(self):
        """collected_tools 追溯信息保留"""
        original = make_state_with_ctx()
        raw = _state_to_dict(original)
        restored = _dict_to_state(raw)

        assert "collect_component_status" in restored.diagnostic_context.collected_tools

    def test_none_diagnostic_context_roundtrip(self):
        """diagnostic_context 为 None 时往返正确"""
        state = WorkflowState(
            session_id="sess_none_ctx",
            current_step=WorkflowStep.ENV_RECOGNISING,
        )
        raw = _state_to_dict(state)
        restored = _dict_to_state(raw)
        assert restored.diagnostic_context is None

    def test_container_runtime_in_env_info(self):
        """EnvInfo.container_runtime 枚举往返正确"""
        original = make_state_with_ctx()
        raw = _state_to_dict(original)
        restored = _dict_to_state(raw)

        assert restored.env_info.container_runtime == ContainerRuntime.KUBERNETES
