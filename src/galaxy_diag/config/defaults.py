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
    max_tokens: int = 1024                  # 默认最大输出 token 数，防止无限生成
    embed_model: str = ""                       # embedding 模型名（空=未启用 RAG）；如 nomic-embed-text


@dataclass
class HardwareRequirement:
    """最低硬件资源要求"""
    min_cpu_cores: int = 4
    min_ram_gb: float = 8.0
    min_gpu_vram_gb: float = 6.0
    min_disk_gb: float = 10.0
    gpu_required: bool = False


@dataclass
class KnowledgeConfig:
    """客户知识库检索配置（REQ-X-02）"""
    top_k: int = 3                  # 检索返回的最大案例数
    min_similarity: float = 0.0     # 最低余弦相似度阈值，0.0=不过滤


@dataclass
class AppConfig:
    """应用全局配置"""
    llm: LLMConfig = field(default_factory=LLMConfig)
    hardware: HardwareRequirement = field(default_factory=HardwareRequirement)
    knowledge: KnowledgeConfig = field(default_factory=KnowledgeConfig)
