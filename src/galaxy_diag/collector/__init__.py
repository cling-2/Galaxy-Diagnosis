"""环境感知模块（REQ-B-01 / REQ-B-02）

顶层编排：识别环境类型 → 采集软硬件 → 组装 EnvInfo。

对齐 Environment_awareness_design.md §顶层编排函数。

TODO: tools.py（LangChain @tool 封装）与 collect_network 待 diagnoser/ 模块
      实现后添加，当前核心采集器设计为 langchain 无关，零返工。
"""

from __future__ import annotations

from galaxy_diag.collector.env_detect import EnvironmentDetector
from galaxy_diag.collector.hardware import HardwareCollector
from galaxy_diag.collector.storage import StorageCollector
from galaxy_diag.shared.types import EnvInfo, EnvironmentType

__all__ = ["collect_env"]

# raw_output 单条截断阈值
_RAW_OUTPUT_MAX_CHARS = 2048


def collect_env() -> EnvInfo:
    """环境感知顶层编排：识别环境类型 → 采集软硬件 → 组装 EnvInfo

    Returns:
        EnvInfo 结构化环境信息

    Raises:
        CollectorError: 采集完全失败（仅环境识别不可判定时）
    """
    warnings: list[str] = []

    # 1. 环境识别
    env_type = EnvironmentDetector().detect(warnings)

    # 2. 硬件采集
    hw_collector = HardwareCollector()
    hardware = hw_collector.collect(env_type, warnings)

    # 3. 存储采集
    st_collector = StorageCollector()
    storage = st_collector.collect(env_type, warnings)

    # 4. 环境类型级别的采集提示
    _append_env_type_warnings(env_type, warnings)

    # 5. 汇总 raw_output（截断）
    raw = {}
    raw.update(hw_collector.raw_output)
    raw.update(st_collector.raw_output)
    raw = _truncate_raw_output(raw)

    return EnvInfo(
        env_type=env_type,
        hardware=hardware,
        storage=storage,
        collection_warnings=warnings,
        raw_output=raw,
    )


def _append_env_type_warnings(env_type: EnvironmentType, warnings: list[str]) -> None:
    """根据环境类型追加采集受限提示"""
    if env_type == EnvironmentType.CONTAINER:
        warnings.append(
            "容器环境无法直接采集宿主机硬件信息（CPU/RAID/物理网卡），"
            "建议在宿主机上执行 galaxy-diag env 补充"
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
