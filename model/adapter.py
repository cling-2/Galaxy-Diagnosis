"""ModelAdapter：统一的 LLM 调用入口

所有模块通过此类与模型交互，基于 openai Python SDK，
兼容 Ollama / vLLM / llama.cpp 等任何 OpenAI 兼容后端。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator

from openai import OpenAI

from config.schema import LLMConfig
from errors import ModelCallError


@dataclass
class ToolCall:
    """工具调用信息"""
    id: str
    name: str
    arguments: str  # JSON 字符串


@dataclass
class ChatResponse:
    """模型响应封装，同时包含文本内容和工具调用"""
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""
    raw: Any = None  # 原始 openai 响应对象，供高级场景使用


class ModelAdapter:
    """统一的 LLM 调用入口

    通过 openai SDK 连接本地推理服务（Ollama / vLLM / llama.cpp），
    提供 chat / chat_stream / chat_with_tools 三种调用方式。
    """

    def __init__(self, config: LLMConfig):
        self.config = config
        self.client = OpenAI(
            base_url=config.base_url,
            api_key=config.api_key,
            timeout=config.timeout,
            max_retries=config.max_retries,
        )

    def chat(self, messages: list[dict[str, str]], **kwargs) -> str:
        """同步调用，返回助手回复文本

        Args:
            messages: OpenAI 格式消息列表，如 [{"role": "user", "content": "..."}]
            **kwargs: 透传给 openai SDK 的额外参数（如 temperature, max_tokens）

        Returns:
            助手回复文本

        Raises:
            ModelCallError: 模型调用失败
        """
        try:
            response = self.client.chat.completions.create(
                model=self.config.model,
                messages=messages,
                **kwargs,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            raise ModelCallError(
                f"模型调用失败: {e}",
                hint=self._call_error_hint(e),
            )

    def chat_stream(self, messages: list[dict[str, str]], **kwargs) -> Iterator[str]:
        """流式调用，返回内容迭代器

        Args:
            messages: OpenAI 格式消息列表
            **kwargs: 透传给 openai SDK 的额外参数

        Yields:
            逐块生成的文本片段

        Raises:
            ModelCallError: 模型调用失败
        """
        try:
            stream = self.client.chat.completions.create(
                model=self.config.model,
                messages=messages,
                stream=True,
                **kwargs,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta
                if delta.content:
                    yield delta.content
        except Exception as e:
            raise ModelCallError(
                f"模型流式调用失败: {e}",
                hint=self._call_error_hint(e),
            )

    def chat_with_tools(
        self,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]],
        **kwargs,
    ) -> ChatResponse:
        """带工具调用的对话

        Args:
            messages: OpenAI 格式消息列表
            tools: OpenAI 格式工具定义列表
            **kwargs: 透传给 openai SDK 的额外参数

        Returns:
            ChatResponse，包含 content 和 tool_calls

        Raises:
            ModelCallError: 模型调用失败
        """
        try:
            response = self.client.chat.completions.create(
                model=self.config.model,
                messages=messages,
                tools=tools,
                **kwargs,
            )
            choice = response.choices[0]
            message = choice.message

            # 解析 tool_calls
            tool_calls = []
            if message.tool_calls:
                for tc in message.tool_calls:
                    tool_calls.append(ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=tc.function.arguments,
                    ))

            return ChatResponse(
                content=message.content or "",
                tool_calls=tool_calls,
                finish_reason=choice.finish_reason or "",
                raw=response,
            )
        except Exception as e:
            raise ModelCallError(
                f"模型工具调用失败: {e}",
                hint=self._call_error_hint(e),
            )

    def _call_error_hint(self, error: Exception) -> str:
        """根据错误类型生成可操作的提示"""
        err_msg = str(error).lower()
        if "timeout" in err_msg or "timed out" in err_msg:
            return "推理超时，纯 CPU 环境建议使用更小模型或增加 timeout 配置"
        if "429" in err_msg or "rate" in err_msg:
            return "模型服务限频，请稍后重试"
        if "connection" in err_msg or "refused" in err_msg:
            return f"无法连接到 {self.config.base_url}，请确认推理服务已启动"
        if "404" in err_msg or "not found" in err_msg:
            return f"模型 '{self.config.model}' 不存在，请确认模型已导入"
        return "请检查推理服务状态和配置"
