"""环境感知模块（REQ-B-01 / REQ-B-02）

顶层编排：识别环境类型 → 采集软硬件 → 组装 EnvInfo。

对齐 Environment_awareness_design.md §顶层编排函数。

TODO: tools.py（LangChain @tool 封装）与 collect_network 待 diagnoser/ 模块
      实现后添加，当前核心采集器设计为 langchain 无关，零返工。
"""

from __future__ import annotations

import shutil

from galaxy_diag.collector.env_detect import EnvironmentDetector, detect_container_runtime
from galaxy_diag.collector.hardware import HardwareCollector
from galaxy_diag.collector.storage import StorageCollector
from galaxy_diag.shared.types import (
    ContainerRuntime,
    EnvInfo,
    EnvironmentType,
    HardwareInfo,
    StorageInfo,
)

__all__ = ["collect_env"]

# raw_output 单条截断阈值
_RAW_OUTPUT_MAX_CHARS = 2048


def collect_env(*, skip_hardware: bool = False) -> EnvInfo:
    """环境感知顶层编排：识别环境类型 → 采集软硬件 → 组装 EnvInfo

    Args:
        skip_hardware: 是否跳过完整硬件和存储采集（C类精简采集）

    Returns:
        EnvInfo 结构化环境信息

    Raises:
        CollectorError: 采集完全失败（仅环境识别不可判定时）
    """
    warnings: list[str] = []

    # 1. 环境类型识别
    env_type = EnvironmentDetector().detect(warnings)

    # 2. 容器运行时子类型识别（仅 CONTAINER 时）
    container_runtime: ContainerRuntime | None = None
    if env_type == EnvironmentType.CONTAINER:
        container_runtime = detect_container_runtime(warnings)

    # 3. 硬件采集（C类：可按需跳过）
    if skip_hardware:
        hardware = HardwareInfo()
        storage: list[StorageInfo] = []
        hw_raw_output: dict[str, str] = {}
        st_raw_output: dict[str, str] = {}
        warnings.append("已跳过完整硬件和存储采集（问题类型不需要）")
    else:
        hw_collector = HardwareCollector()
        hardware = hw_collector.collect(env_type, warnings)

        st_collector = StorageCollector()
        storage = st_collector.collect(env_type, warnings)

        hw_raw_output = hw_collector.raw_output
        st_raw_output = st_collector.raw_output

    # 4. 环境类型级别的采集提示
    _append_env_type_warnings(env_type, container_runtime, warnings)

    # 5. 汇总 raw_output（截断）
    raw = {}
    raw.update(hw_raw_output)
    raw.update(st_raw_output)
    raw = _truncate_raw_output(raw)

    # 6. 容器内 CLI 可用性检测（容器内通常无 docker/kubectl CLI）
    has_docker_cli = shutil.which("docker") is not None
    has_kubectl_cli = shutil.which("kubectl") is not None

    return EnvInfo(
        env_type=env_type,
        container_runtime=container_runtime,
        has_docker_cli=has_docker_cli,
        has_kubectl_cli=has_kubectl_cli,
        hardware=hardware,
        storage=storage,
        collection_warnings=warnings,
        raw_output=raw,
    )


def _append_env_type_warnings(
    env_type: EnvironmentType,
    container_runtime: ContainerRuntime | None,
    warnings: list[str],
) -> None:
    """根据环境类型追加采集受限提示"""
    if env_type == EnvironmentType.CONTAINER:
        runtime_label = {
            ContainerRuntime.DOCKER: "Docker",
            ContainerRuntime.KUBERNETES: "Kubernetes",
            ContainerRuntime.UNKNOWN: "未知",
            None: "未知",
        }[container_runtime]
        warnings.append(
            f"容器环境（运行时: {runtime_label}）无法直接采集宿主机硬件信息"
            f"（CPU/RAID/物理网卡），建议在宿主机上执行 galaxy-diag env 补充"
        )
    elif env_type == EnvironmentType.VM:
        # VM 下 RAID 卡可能透传不可见，但这是一个"可能"而非"确定"，
        # 只在采集结果为空时由 HardwareCollector 自行提示
        pass


def _truncate_raw_output(raw: dict[str, str]) -> dict[str, str]:
    """截断 raw_output 中的长条目，避免注入 LLM 上下文过大"""
    truncated: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(value, str):
            value = str(value)
        if len(value) > _RAW_OUTPUT_MAX_CHARS:
            value = value[:_RAW_OUTPUT_MAX_CHARS] + "\n[truncated]"
        truncated[key] = value
    return truncated
