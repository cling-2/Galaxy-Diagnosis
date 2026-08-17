"""模型资源画像推导器

从模型名解析参数量，按参数量推导最低硬件要求。
纯函数、无副作用、可独立测试。

对齐任务书："7B 模型量化后约 4–5GB 显存/内存；1.5B–3B 纯 CPU 也能流畅跑。"

优先级：config.yaml 显式 hardware 字段 > 模型推导值 > 保守默认值（解析失败时）。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from galaxy_diag.config.defaults import HardwareRequirement

# Ollama 模型名参数量正则：匹配 :1.7b / :8b / :70b 等
# 支持 qwen3:1.7b, llama3:70b, qwen2.5:3b, gemma2:9b 等
_MODEL_SIZE_RE = re.compile(r"[:/](\d+(?:\.\d+)?)b\b", re.IGNORECASE)

# ===== 保守默认值（解析失败时使用，与原 HardwareRequirement 默认值一致）=====
_FALLBACK_CPU_CORES = 4
_FALLBACK_RAM_GB = 8.0
_FALLBACK_GPU_VRAM_GB = 6.0
_FALLBACK_DISK_GB = 10.0
_FALLBACK_GPU_REQUIRED = False

# ===== 推导公式系数 =====
# Q4_K_M 量化每 B 参数约占 0.55 GB（0.5GB 量化权重 + KV cache 余量）
_VRAM_PER_B = 0.55   # GB/B
_VRAM_BASE = 1.0     # KV cache 基础余量 GB
_RAM_OVERHEAD = 2.0  # 纯 CPU 模式额外内存（系统+上下文）GB
_RAM_MIN = 4.0       # 最低内存 GB
_DISK_PER_B = 0.9    # 模型文件 + 余量 GB/B（0.6GB/B × 1.5）
_DISK_BASE = 2.0     # 系统日志/临时文件 GB


def parse_model_size(model: str) -> float | None:
    """从模型名解析参数量（B）

    支持的格式：
      - Ollama 风格：qwen3:1.7b, qwen3:8b, llama3:70b, qwen2.5:3b
      - 斜杠风格：qwen3/1.7b（某些模型仓库）

    Args:
        model: 模型名称

    Returns:
        参数量（B），如 1.7, 8.0, 70.0；无法识别返回 None
    """
    m = _MODEL_SIZE_RE.search(model)
    if m:
        return float(m.group(1))
    return None


def derive_hardware_requirement(model: str) -> "HardwareRequirement":
    """根据模型名推导最低硬件要求

    推导规则（对齐任务书 "7B≈4-5GB 显存/内存"）：

    | 字段              | 公式                          | 1.7B | 7B   | 8B  |
    |-------------------|-------------------------------|------|------|-----|
    | min_gpu_vram_gb   | N×0.55 + 1.0                  | 1.94 | 4.85 | 5.4 |
    | min_ram_gb        | max(vram+2.0, 4.0)            | 4.0  | 6.85 | 7.4 |
    | min_disk_gb       | N×0.9 + 2.0                   | 3.53 | 8.3  | 9.2 |
    | min_cpu_cores     | <3B→2, 3-7B→4, >7B→8          | 2    | 4    | 8   |
    | gpu_required      | N≥14 → True                   | False| False| False|

    解析失败（如自定义模型名 my-model）返回保守默认 HardwareRequirement()。

    Args:
        model: 模型名称（如 qwen3:8b）

    Returns:
        HardwareRequirement: 推导出的最低硬件要求
    """
    from galaxy_diag.config.defaults import HardwareRequirement

    size_b = parse_model_size(model)
    if size_b is None:
        # 解析失败：用保守默认值
        return HardwareRequirement(
            min_cpu_cores=_FALLBACK_CPU_CORES,
            min_ram_gb=_FALLBACK_RAM_GB,
            min_gpu_vram_gb=_FALLBACK_GPU_VRAM_GB,
            min_disk_gb=_FALLBACK_DISK_GB,
            gpu_required=_FALLBACK_GPU_REQUIRED,
        )

    # 推导各指标
    vram_gb = size_b * _VRAM_PER_B + _VRAM_BASE
    ram_gb = max(vram_gb + _RAM_OVERHEAD, _RAM_MIN)
    disk_gb = size_b * _DISK_PER_B + _DISK_BASE

    if size_b < 3.0:
        cpu_cores = 2
    elif size_b <= 7.0:
        cpu_cores = 4
    else:
        cpu_cores = 8

    gpu_required = size_b >= 14.0

    return HardwareRequirement(
        min_cpu_cores=cpu_cores,
        min_ram_gb=round(ram_gb, 1),
        min_gpu_vram_gb=round(vram_gb, 2),
        min_disk_gb=round(disk_gb, 1),
        gpu_required=gpu_required,
    )
