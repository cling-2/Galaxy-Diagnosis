"""推理服务健康检查

检查本地推理服务是否就绪、目标模型是否可用：
  1. 服务可达性（请求模型列表端点）
  2. 模型存在性（验证目标模型在可用列表中）
  3. 推理可用性（发送简单请求验证模型能响应）
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from config.schema import LLMConfig
from model.adapter import ModelAdapter


@dataclass
class HealthResult:
    """健康检查结果"""
    ok: bool
    message: str
    available_models: list[str] = None
    hint: str = ""


class HealthChecker:
    """推理服务健康检查器"""

    def __init__(self, config: LLMConfig):
        self.config = config
        # 推导 Ollama 服务根地址（去掉 /v1 后缀），用于原生 API
        # 例：http://localhost:11434/v1 -> http://localhost:11434
        self.service_root = config.base_url.rstrip("/")
        if self.service_root.endswith("/v1"):
            self.service_root = self.service_root[:-3]

    def check(self) -> HealthResult:
        """执行三步健康检查

        Returns:
            HealthResult，ok=True 表示推理服务就绪
        """
        # Step 1: 服务可达性 + 拉取模型列表
        models, error = self._fetch_models()
        if error is not None:
            return HealthResult(
                ok=False,
                message=error,
                available_models=[],
            )

        # Step 2: 模型存在性
        if self.config.model not in models:
            hint_models = ", ".join(models) if models else "(无可用模型)"
            return HealthResult(
                ok=False,
                message=(
                    f"模型 '{self.config.model}' 未找到。"
                    f"可用模型: {hint_models}"
                ),
                available_models=models,
                hint="请先导入模型: ollama create qwen3:8b -f Modelfile",
            )

        # Step 3: 推理可用性
        inference_ok, inference_msg = self._test_inference()
        if not inference_ok:
            return HealthResult(
                ok=False,
                message=f"模型推理测试失败: {inference_msg}",
                available_models=models,
            )

        return HealthResult(
            ok=True,
            message=f"推理服务就绪，模型: {self.config.model}",
            available_models=models,
        )

    def _fetch_models(self) -> tuple[list[str], str | None]:
        """拉取可用模型列表

        同时尝试 Ollama 原生 /api/tags 和 OpenAI 兼容 /v1/models，
        适配不同部署方式（直接 ollama serve 或 /v1 代理）。

        Returns:
            (模型名列表, 错误信息)；错误信息非 None 表示拉取失败
        """
        # 尝试 Ollama 原生 /api/tags
        try:
            resp = httpx.get(
                f"{self.service_root}/api/tags",
                timeout=5,
            )
            if resp.status_code == 200:
                data = resp.json()
                models = [m["name"] for m in data.get("models", [])]
                return models, None
        except Exception:
            pass  # 回退到 OpenAI 兼容端点

        # 尝试 OpenAI 兼容 /v1/models
        try:
            resp = httpx.get(
                f"{self.config.base_url.rstrip('/')}/models",
                timeout=5,
                headers={"Authorization": f"Bearer {self.config.api_key}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                models = [m["id"] for m in data.get("data", [])]
                return models, None
            return [], (
                f"推理服务返回异常状态码 {resp.status_code}，"
                f"请确认 {self.service_root} 处的推理服务（如 Ollama）已启动"
            )
        except httpx.ConnectError:
            return [], (
                f"无法连接到推理服务 {self.service_root}，"
                f"请确认 Ollama 已启动: systemctl status ollama"
            )
        except Exception as e:
            return [], f"连接推理服务失败: {e}，请确认 Ollama 已启动"

    def _test_inference(self) -> tuple[bool, str]:
        """发送简单请求验证模型能实际响应

        Returns:
            (是否成功, 错误信息)
        """
        try:
            adapter = ModelAdapter(self.config)
            reply = adapter.chat(
                messages=[
                    {"role": "system", "content": "你是一个助手，请用一个字回复。"},
                    {"role": "user", "content": "hi"},
                ],
                max_tokens=8,
                timeout=30,
            )
            if reply is None:
                return False, "模型返回空响应"
            return True, ""
        except Exception as e:
            return False, str(e)
