"""配置加载：YAML → 环境变量覆盖 → 默认值

加载优先级：
  1. YAML 配置文件
  2. 环境变量覆盖（前缀 GALAXY_，如 GALAXY_LLM_BASE_URL）
  3. 代码默认值（schema.py 中的 dataclass 默认值）
"""

import os
from typing import Any

import yaml

from config.schema import AppConfig, LLMConfig, HardwareRequirement
from errors import ConfigError


# 环境变量到配置字段的映射
_ENV_MAPPING: dict[str, tuple[str, str]] = {
    # (环境变量名, (配置段, 字段名))
    "GALAXY_LLM_BASE_URL": ("llm", "base_url"),
    "GALAXY_LLM_MODEL": ("llm", "model"),
    "GALAXY_LLM_API_KEY": ("llm", "api_key"),
    "GALAXY_LLM_TIMEOUT": ("llm", "timeout"),
    "GALAXY_LLM_MAX_RETRIES": ("llm", "max_retries"),
    "GALAXY_HW_MIN_CPU_CORES": ("hardware", "min_cpu_cores"),
    "GALAXY_HW_MIN_RAM_GB": ("hardware", "min_ram_gb"),
    "GALAXY_HW_MIN_GPU_VRAM_GB": ("hardware", "min_gpu_vram_gb"),
    "GALAXY_HW_MIN_DISK_GB": ("hardware", "min_disk_gb"),
    "GALAXY_HW_GPU_REQUIRED": ("hardware", "gpu_required"),
}

def _parse_bool(value: str) -> bool:
    """将环境变量字符串解析为布尔值"""
    return value.lower() in ("true", "1", "yes")


# 需要类型转换的字段
_TYPE_COERCIONS: dict[tuple[str, str], type] = {
    ("llm", "timeout"): int,
    ("llm", "max_retries"): int,
    ("hardware", "min_cpu_cores"): int,
    ("hardware", "min_ram_gb"): float,
    ("hardware", "min_gpu_vram_gb"): float,
    ("hardware", "min_disk_gb"): float,
    ("hardware", "gpu_required"): _parse_bool,
}


def load_config(config_path: str = "config.yaml") -> AppConfig:
    """加载配置：YAML → 环境变量覆盖 → 默认值

    Args:
        config_path: YAML 配置文件路径

    Returns:
        AppConfig 实例

    Raises:
        ConfigError: 配置文件格式错误或缺少必填字段
    """
    # 1. 从 YAML 文件加载（文件不存在则使用默认值）
    raw: dict[str, Any] = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            raise ConfigError(
                f"配置文件格式错误: {config_path}",
                hint=f"请检查 YAML 语法: {e}",
            )
        except OSError as e:
            raise ConfigError(
                f"无法读取配置文件: {config_path}",
                hint=f"请确认文件存在且可读: {e}",
            )

    # 2. 环境变量覆盖
    for env_key, (section, field_name) in _ENV_MAPPING.items():
        env_val = os.environ.get(env_key)
        if env_val is not None:
            # 确保该 section 存在
            if section not in raw:
                raw[section] = {}
            # 类型转换
            coercion = _TYPE_COERCIONS.get((section, field_name))
            if coercion:
                try:
                    raw[section][field_name] = coercion(env_val)
                except (ValueError, TypeError):
                    raise ConfigError(
                        f"环境变量 {env_key}={env_val!r} 类型错误",
                        hint=f"期望类型: {coercion.__name__}",
                    )
            else:
                raw[section][field_name] = env_val

    # 3. 构建配置对象
    try:
        llm_raw = raw.get("llm", {})
        hw_raw = raw.get("hardware", {})

        llm_config = LLMConfig(**_filter_known_fields(llm_raw, LLMConfig))
        hw_config = HardwareRequirement(**_filter_known_fields(hw_raw, HardwareRequirement))

        return AppConfig(llm=llm_config, hardware=hw_config)
    except TypeError as e:
        raise ConfigError(
            f"配置字段错误: {e}",
            hint="请检查 config.yaml 中的字段名是否正确",
        )


def _filter_known_fields(raw: dict, cls: type) -> dict:
    """过滤掉数据类中不存在的字段，防止意外关键字参数"""
    import dataclasses
    if dataclasses.is_dataclass(cls):
        known = {f.name for f in dataclasses.fields(cls)}
        return {k: v for k, v in raw.items() if k in known}
    return raw
