"""配置加载与数据类测试

覆盖：
  - defaults.py 数据类默认值
  - settings.py YAML 加载、环境变量覆盖、默认值回退
  - ConfigError 错误处理
"""

import os
import textwrap

import pytest

from galaxy_diag.config.defaults import AppConfig, LLMConfig, HardwareRequirement
from galaxy_diag.config.settings import load_config
from galaxy_diag.shared.errors import ConfigError


# ============================================================
# defaults.py 数据类
# ============================================================

class TestLLMConfig:
    def test_defaults(self):
        cfg = LLMConfig()
        assert cfg.base_url == "http://localhost:11434/v1"
        assert cfg.model == "qwen3:8b"
        assert cfg.api_key == "ollama"
        assert cfg.timeout == 120
        assert cfg.max_retries == 3

    def test_custom_values(self):
        cfg = LLMConfig(base_url="http://x:8080/v1", model="llama3", timeout=60)
        assert cfg.base_url == "http://x:8080/v1"
        assert cfg.model == "llama3"
        assert cfg.timeout == 60
        assert cfg.api_key == "ollama"  # 未覆盖保持默认


class TestHardwareRequirement:
    def test_defaults(self):
        hw = HardwareRequirement()
        assert hw.min_cpu_cores == 4
        assert hw.min_ram_gb == 8.0
        assert hw.min_gpu_vram_gb == 6.0
        assert hw.min_disk_gb == 10.0
        assert hw.gpu_required is False


class TestAppConfig:
    def test_defaults(self):
        cfg = AppConfig()
        assert isinstance(cfg.llm, LLMConfig)
        assert isinstance(cfg.hardware, HardwareRequirement)


# ============================================================
# settings.py 配置加载
# ============================================================

class TestLoadConfigYAML:
    """从 YAML 文件加载"""

    def test_load_config_yaml(self, tmp_path):
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(textwrap.dedent("""\
            llm:
              base_url: "http://192.168.1.100:11434/v1"
              model: "qwen3:8b"
            hardware:
              min_cpu_cores: 8
              min_ram_gb: 16.0
        """))
        cfg = load_config(str(yaml_file))
        assert cfg.llm.base_url == "http://192.168.1.100:11434/v1"
        assert cfg.llm.model == "qwen3:8b"
        assert cfg.hardware.min_cpu_cores == 8
        assert cfg.hardware.min_ram_gb == 16.0

    def test_load_partial_yaml_keeps_defaults(self, tmp_path):
        """YAML 只填部分字段，其余保持默认值"""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text("llm:\n  model: llama3\n")
        cfg = load_config(str(yaml_file))
        assert cfg.llm.model == "llama3"
        assert cfg.llm.base_url == "http://localhost:11434/v1"  # 默认值
        assert cfg.hardware.min_cpu_cores == 4  # 默认值

    def test_load_nonexistent_file_uses_defaults(self, tmp_path):
        """配置文件不存在时使用默认值（model 默认 qwen3:8b，hardware 按模型推导）"""
        cfg = load_config(str(tmp_path / "nonexistent.yaml"))
        assert cfg.llm.model == "qwen3:8b"
        # 8B 模型推导：cpu=8, vram≈5.4GB（非固定默认值 4/6）
        assert cfg.hardware.min_cpu_cores == 8
        assert cfg.hardware.min_gpu_vram_gb == 5.4

    def test_load_invalid_yaml_raises_config_error(self, tmp_path):
        """YAML 语法错误抛 ConfigError"""
        yaml_file = tmp_path / "bad.yaml"
        yaml_file.write_text("llm:\n  model: [invalid")
        with pytest.raises(ConfigError, match="配置文件格式错误"):
            load_config(str(yaml_file))

    def test_extra_fields_ignored(self, tmp_path):
        """YAML 中的多余字段不影响加载"""
        yaml_file = tmp_path / "extra.yaml"
        yaml_file.write_text("llm:\n  model: qwen3:8b\n  unknown_field: xxx\n")
        cfg = load_config(str(yaml_file))
        assert cfg.llm.model == "qwen3:8b"


class TestLoadConfigEnvOverride:
    """环境变量覆盖"""

    def test_env_overrides_yaml(self, tmp_path):
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text("llm:\n  model: qwen3:8b\n")
        os.environ["GALAXY_LLM_MODEL"] = "llama3"
        try:
            cfg = load_config(str(yaml_file))
            assert cfg.llm.model == "llama3"
        finally:
            del os.environ["GALAXY_LLM_MODEL"]

    def test_env_type_coercion_int(self, tmp_path):
        """整数类型环境变量正确转换"""
        os.environ["GALAXY_HW_MIN_CPU_CORES"] = "16"
        try:
            cfg = load_config(str(tmp_path / "nonexistent.yaml"))
            assert cfg.hardware.min_cpu_cores == 16
        finally:
            del os.environ["GALAXY_HW_MIN_CPU_CORES"]

    def test_env_type_coercion_float(self, tmp_path):
        """浮点类型环境变量正确转换"""
        os.environ["GALAXY_HW_MIN_RAM_GB"] = "32.5"
        try:
            cfg = load_config(str(tmp_path / "nonexistent.yaml"))
            assert cfg.hardware.min_ram_gb == 32.5
        finally:
            del os.environ["GALAXY_HW_MIN_RAM_GB"]

    def test_env_type_coercion_bool(self, tmp_path):
        """布尔类型环境变量正确转换"""
        os.environ["GALAXY_HW_GPU_REQUIRED"] = "true"
        try:
            cfg = load_config(str(tmp_path / "nonexistent.yaml"))
            assert cfg.hardware.gpu_required is True
        finally:
            del os.environ["GALAXY_HW_GPU_REQUIRED"]

    def test_env_invalid_type_raises_config_error(self, tmp_path):
        """类型转换失败抛 ConfigError"""
        os.environ["GALAXY_HW_MIN_CPU_CORES"] = "not_a_number"
        try:
            with pytest.raises(ConfigError, match="类型错误"):
                load_config(str(tmp_path / "nonexistent.yaml"))
        finally:
            del os.environ["GALAXY_HW_MIN_CPU_CORES"]

    def test_zero_external_addresses(self, tmp_path):
        """配置中无外网硬编码地址"""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(textwrap.dedent("""\
            llm:
              base_url: "http://localhost:11434/v1"
              model: "qwen3:8b"
        """))
        cfg = load_config(str(yaml_file))
        # localhost 不是外网地址
        assert "localhost" in cfg.llm.base_url
        assert "127.0.0.1" in cfg.llm.base_url or "localhost" in cfg.llm.base_url


def test_llm_config_has_embed_model_default_empty():
    from galaxy_diag.config.defaults import LLMConfig
    cfg = LLMConfig()
    assert cfg.embed_model == ""


def test_app_config_has_knowledge_defaults():
    from galaxy_diag.config.defaults import AppConfig, KnowledgeConfig
    cfg = AppConfig()
    assert isinstance(cfg.knowledge, KnowledgeConfig)
    assert cfg.knowledge.top_k == 3
    assert cfg.knowledge.min_similarity == 0.0


def test_load_config_reads_embed_model_and_knowledge(tmp_path):
    import yaml
    from galaxy_diag.config.settings import load_config
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text(yaml.dump({
        "llm": {"model": "qwen3:1.7b", "embed_model": "nomic-embed-text"},
        "knowledge": {"top_k": 5, "min_similarity": 0.7},
    }), encoding="utf-8")
    cfg = load_config(str(cfg_file))
    assert cfg.llm.embed_model == "nomic-embed-text"
    assert cfg.knowledge.top_k == 5
    assert cfg.knowledge.min_similarity == 0.7
