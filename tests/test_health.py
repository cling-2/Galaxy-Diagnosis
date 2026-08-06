"""健康检查测试

使用 mock 替换 HTTP 请求和 ModelAdapter，不依赖真实 Ollama。
"""

from unittest.mock import patch, MagicMock

import pytest

from galaxy_diag.config.defaults import LLMConfig
from galaxy_diag.model.health import HealthChecker, HealthResult


@pytest.fixture
def config():
    return LLMConfig(base_url="http://localhost:11434/v1", model="qwen3:8b")


@pytest.fixture
def checker(config):
    return HealthChecker(config)


# ============================================================
# Step 1: 服务可达性 + 模型列表拉取
# ============================================================

class TestFetchModels:
    def test_ollama_native_api_success(self, checker):
        """Ollama 原生 /api/tags 成功"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "models": [
                {"name": "qwen3:8b"},
                {"name": "llama3:7b"},
            ]
        }
        with patch("galaxy_diag.model.health.httpx.get", return_value=mock_resp):
            result = checker.check()
        # 应该通过 step1，进入 step2（模型存在性检查）
        assert result.available_models == ["qwen3:8b", "llama3:7b"]

    def test_openai_compat_fallback(self, checker):
        """Ollama 原生失败，回退到 /v1/models"""
        ollama_resp = MagicMock()
        ollama_resp.status_code = 404  # 原生不可用

        compat_resp = MagicMock()
        compat_resp.status_code = 200
        compat_resp.json.return_value = {
            "data": [
                {"id": "qwen3:8b"},
                {"id": "llama3:7b"},
            ]
        }

        with patch("galaxy_diag.model.health.httpx.get", side_effect=[ollama_resp, compat_resp]):
            result = checker.check()
        assert "qwen3:8b" in result.available_models

    def test_service_unreachable(self, checker):
        """服务不可达"""
        import httpx as real_httpx
        with patch("galaxy_diag.model.health.httpx.get", side_effect=real_httpx.ConnectError("refused")):
            result = checker.check()
        assert result.ok is False
        assert "无法连接" in result.message or "连接" in result.message


# ============================================================
# Step 2: 模型存在性
# ============================================================

class TestModelExistence:
    def test_model_not_found_lists_available(self, checker):
        """模型不存在时列出可用模型"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "models": [{"name": "llama3:7b"}]
        }
        with patch("galaxy_diag.model.health.httpx.get", return_value=mock_resp):
            result = checker.check()
        assert result.ok is False
        assert "未找到" in result.message
        assert "llama3:7b" in result.available_models

    def test_no_models_available(self, checker):
        """服务可达但无任何模型"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"models": []}
        with patch("galaxy_diag.model.health.httpx.get", return_value=mock_resp):
            result = checker.check()
        assert result.ok is False
        assert "未找到" in result.message


# ============================================================
# Step 3: 推理可用性
# ============================================================

class TestInference:
    def test_full_check_pass(self, checker):
        """三步全部通过"""
        # Step 1: 模型列表
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"models": [{"name": "qwen3:8b"}]}

        # Step 3: 推理成功
        with patch("galaxy_diag.model.health.httpx.get", return_value=mock_resp), \
             patch.object(checker, "_test_inference", return_value=(True, "")):
            result = checker.check()
        assert result.ok is True
        assert "就绪" in result.message

    def test_inference_failure(self, checker):
        """服务可达 + 模型存在，但推理失败"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"models": [{"name": "qwen3:8b"}]}

        with patch("galaxy_diag.model.health.httpx.get", return_value=mock_resp), \
             patch.object(checker, "_test_inference", return_value=(False, "推理超时")):
            result = checker.check()
        assert result.ok is False
        assert "推理" in result.message


# ============================================================
# HealthResult 数据结构
# ============================================================

class TestHealthResult:
    def test_default_fields(self):
        r = HealthResult(ok=True, message="就绪")
        assert r.ok is True
        assert r.available_models is None
        assert r.hint == ""

    def test_with_all_fields(self):
        r = HealthResult(
            ok=False,
            message="模型不存在",
            available_models=["llama3:7b"],
            hint="请先导入模型",
        )
        assert r.available_models == ["llama3:7b"]
        assert r.hint == "请先导入模型"
