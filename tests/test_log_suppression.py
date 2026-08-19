"""_apply_log_suppression_env 日志抑制环境变量测试

验证防御性设置：未设置时填充默认值、已设置时保留用户值。
"""

from __future__ import annotations

import os

import pytest


class TestLogSuppressionEnv:
    """_apply_log_suppression_env 防御性环境变量设置"""

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        """每个测试前清理三变量，确保测试隔离"""
        for var in ("OLLAMA_LOG_LEVEL", "LLAMA_LOG_LEVEL", "GIN_MODE"):
            monkeypatch.delenv(var, raising=False)

    def test_sets_defaults_when_unset(self):
        """三变量均未设置时填充默认值"""
        from galaxy_diag.workflow.cli.app import _apply_log_suppression_env
        _apply_log_suppression_env()
        assert os.environ["OLLAMA_LOG_LEVEL"] == "ERROR"
        assert os.environ["LLAMA_LOG_LEVEL"] == "3"
        assert os.environ["GIN_MODE"] == "release"

    def test_respects_existing_ollama_log_level(self, monkeypatch):
        """OLLAMA_LOG_LEVEL 已设置时保留用户值"""
        monkeypatch.setenv("OLLAMA_LOG_LEVEL", "DEBUG")
        from galaxy_diag.workflow.cli.app import _apply_log_suppression_env
        _apply_log_suppression_env()
        assert os.environ["OLLAMA_LOG_LEVEL"] == "DEBUG"

    def test_respects_existing_llama_log_level(self, monkeypatch):
        """LLAMA_LOG_LEVEL 已设置时保留用户值"""
        monkeypatch.setenv("LLAMA_LOG_LEVEL", "0")
        from galaxy_diag.workflow.cli.app import _apply_log_suppression_env
        _apply_log_suppression_env()
        assert os.environ["LLAMA_LOG_LEVEL"] == "0"

    def test_respects_existing_gin_mode(self, monkeypatch):
        """GIN_MODE 已设置时保留用户值"""
        monkeypatch.setenv("GIN_MODE", "debug")
        from galaxy_diag.workflow.cli.app import _apply_log_suppression_env
        _apply_log_suppression_env()
        assert os.environ["GIN_MODE"] == "debug"

    def test_idempotent(self):
        """重复调用幂等（不改变已设值）"""
        from galaxy_diag.workflow.cli.app import _apply_log_suppression_env
        _apply_log_suppression_env()
        first_ollama = os.environ["OLLAMA_LOG_LEVEL"]
        first_llama = os.environ["LLAMA_LOG_LEVEL"]
        first_gin = os.environ["GIN_MODE"]
        _apply_log_suppression_env()
        assert os.environ["OLLAMA_LOG_LEVEL"] == first_ollama
        assert os.environ["LLAMA_LOG_LEVEL"] == first_llama
        assert os.environ["GIN_MODE"] == first_gin
