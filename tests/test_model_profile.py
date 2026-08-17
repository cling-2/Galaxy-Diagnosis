"""模型资源画像推导器测试 (config/model_profile.py)

覆盖：
- parse_model_size: 各模型名格式解析 + 无法识别返回 None
- derive_hardware_requirement: 各参数量推导值正确 + 解析失败兜底
- 对齐任务书 "7B≈4-5GB 显存/内存"
"""

from __future__ import annotations

import pytest

from galaxy_diag.config.defaults import HardwareRequirement
from galaxy_diag.config.model_profile import (
    derive_hardware_requirement,
    parse_model_size,
)


class TestParseModelSize:
    """模型名参数量解析"""

    @pytest.mark.parametrize("model,expected", [
        ("qwen3:1.7b", 1.7),
        ("qwen3:8b", 8.0),
        ("qwen3:0.5b", 0.5),
        ("llama3:70b", 70.0),
        ("qwen2.5:3b", 3.0),
        ("gemma2:9b", 9.0),
        ("qwen3:14b", 14.0),
        ("qwen3:32b", 32.0),
    ])
    def test_parse_known_models(self, model, expected):
        assert parse_model_size(model) == expected

    def test_parse_case_insensitive(self):
        assert parse_model_size("qwen3:8B") == 8.0
        assert parse_model_size("LLAMA3:7B") == 7.0

    def test_parse_slash_format(self):
        assert parse_model_size("qwen3/1.7b") == 1.7

    @pytest.mark.parametrize("model", [
        "my-custom-model",      # 无参数量后缀
        "llama3",               # 只有名字
        "qwen3:latest",         # tag 非参数量
        "",                     # 空
    ])
    def test_parse_unrecognized_returns_none(self, model):
        assert parse_model_size(model) is None


class TestDeriveHardwareRequirement:
    """根据模型推导硬件要求"""

    def test_8b_model_derived_values(self):
        """8B 模型推导（对齐任务书 7B≈4-5GB）"""
        hw = derive_hardware_requirement("qwen3:8b")
        assert isinstance(hw, HardwareRequirement)
        assert hw.min_cpu_cores == 8        # >7B → 8 核
        assert hw.min_gpu_vram_gb == 5.4    # 8×0.55+1.0
        assert hw.min_ram_gb == 7.4         # max(5.4+2.0, 4.0)
        assert hw.min_disk_gb == 9.2        # 8×0.9+2.0
        assert hw.gpu_required is False     # <14B

    def test_7b_model_aligns_with_taskbook(self):
        """7B 模型显存应落在任务书所述 4-5GB 区间"""
        hw = derive_hardware_requirement("qwen3:7b")
        assert 4.0 <= hw.min_gpu_vram_gb <= 5.0   # 7×0.55+1.0=4.85
        assert hw.min_cpu_cores == 4        # 3-7B → 4 核

    def test_1_7b_small_model(self):
        """1.7B 小模型要求低（纯 CPU 可流畅跑）"""
        hw = derive_hardware_requirement("qwen3:1.7b")
        assert hw.min_cpu_cores == 2        # <3B → 2 核
        assert hw.min_gpu_vram_gb == 1.94   # 1.7×0.55+1.0
        assert hw.min_ram_gb == 4.0         # max(1.94+2.0, 4.0)=4.0
        assert hw.min_disk_gb == 3.5        # 1.7×0.9+2.0≈3.53→round 1
        assert hw.gpu_required is False

    def test_14b_requires_gpu(self):
        """14B 及以上要求 GPU"""
        hw = derive_hardware_requirement("qwen3:14b")
        assert hw.gpu_required is True
        assert hw.min_cpu_cores == 8        # >7B

    def test_3b_boundary(self):
        """3B 边界：3-7B 档 → 4 核"""
        hw = derive_hardware_requirement("qwen3:3b")
        assert hw.min_cpu_cores == 4

    def test_unrecognized_model_falls_back(self):
        """无法识别参数量时用保守默认值"""
        hw = derive_hardware_requirement("my-custom-model")
        assert hw.min_cpu_cores == 4
        assert hw.min_ram_gb == 8.0
        assert hw.min_gpu_vram_gb == 6.0
        assert hw.min_disk_gb == 10.0
        assert hw.gpu_required is False

    def test_empty_model_falls_back(self):
        """空模型名用保守默认值"""
        hw = derive_hardware_requirement("")
        assert hw.min_cpu_cores == 4
        assert hw.min_gpu_vram_gb == 6.0


class TestLoadConfigDerivation:
    """load_config 中模型推导与手动配置的优先级"""

    def test_hw_derived_when_not_configured(self, tmp_path):
        """config.yaml 未配 hardware 段 → 按模型推导"""
        import textwrap
        from galaxy_diag.config.settings import load_config

        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(textwrap.dedent("""\
            llm:
              model: "qwen3:1.7b"
        """))
        cfg = load_config(str(yaml_file))
        # 1.7B 推导值
        assert cfg.hardware.min_cpu_cores == 2
        assert cfg.hardware.min_gpu_vram_gb == 1.94

    def test_explicit_hw_overrides_derivation(self, tmp_path):
        """config.yaml 显式 hardware 字段优先于推导"""
        import textwrap
        from galaxy_diag.config.settings import load_config

        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(textwrap.dedent("""\
            llm:
              model: "qwen3:1.7b"
            hardware:
              min_cpu_cores: 16
        """))
        cfg = load_config(str(yaml_file))
        # min_cpu_cores 用户显式 → 16（覆盖推导的 2）
        assert cfg.hardware.min_cpu_cores == 16
        # 未显式配置的字段仍走推导
        assert cfg.hardware.min_gpu_vram_gb == 1.94
