"""配置数据类定义"""

from dataclasses import dataclass, field


@dataclass
class LLMConfig:
    """LLM 推理服务配置"""
    base_url: str = "http://localhost:11434/v1"
    model: str = "qwen3:8b"
    api_key: str = "ollama"
    timeout: int = 120
    max_retries: int = 3


@dataclass
class HardwareRequirement:
    """最低硬件资源要求"""
    min_cpu_cores: int = 4
    min_ram_gb: float = 8.0
    min_gpu_vram_gb: float = 6.0
    min_disk_gb: float = 10.0
    gpu_required: bool = False


@dataclass
class AppConfig:
    """应用全局配置"""
    llm: LLMConfig = field(default_factory=LLMConfig)
    hardware: HardwareRequirement = field(default_factory=HardwareRequirement)
