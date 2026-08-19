"""ModelAdapter 测试

使用 mock 替换 OpenAI client，验证 chat / chat_stream / chat_with_tools
及错误处理，不依赖真实推理服务。
"""

from unittest.mock import MagicMock, patch

import pytest

from galaxy_diag.config.defaults import LLMConfig
from galaxy_diag.model.client import ModelAdapter, ChatResponse, ToolCall
from galaxy_diag.shared.errors import ModelCallError


def make_mock_response(content="", tool_calls=None, finish_reason="stop"):
    """构造 mock 的 OpenAI 响应对象"""
    message = MagicMock()
    message.content = content
    message.tool_calls = tool_calls
    choice = MagicMock()
    choice.message = message
    choice.finish_reason = finish_reason
    response = MagicMock()
    response.choices = [choice]
    return response


def make_mock_tool_call(id_="call_1", name="check_disk", arguments='{"path":"/"}'):
    """构造 mock 的单个 tool_call"""
    tc = MagicMock()
    tc.id = id_
    tc.function.name = name
    tc.function.arguments = arguments
    return tc


@pytest.fixture
def adapter():
    """返回一个 client 被 mock 的 ModelAdapter"""
    cfg = LLMConfig()
    with patch("galaxy_diag.model.client.OpenAI") as mock_openai:
        a = ModelAdapter(cfg)
        a.client = MagicMock()
        yield a


# ============================================================
# chat
# ============================================================

class TestChat:
    def test_chat_returns_content(self, adapter):
        adapter.client.chat.completions.create.return_value = make_mock_response(
            content="银河平台是金山云的私有云平台"
        )
        reply = adapter.chat([{"role": "user", "content": "介绍银河平台"}])
        assert reply == "银河平台是金山云的私有云平台"

    def test_chat_passes_model_and_messages(self, adapter):
        adapter.client.chat.completions.create.return_value = make_mock_response(
            content="ok"
        )
        adapter.chat([{"role": "user", "content": "hi"}], temperature=0.7)
        call_kwargs = adapter.client.chat.completions.create.call_args
        assert call_kwargs.kwargs["model"] == "qwen3:8b"
        assert call_kwargs.kwargs["messages"] == [{"role": "user", "content": "hi"}]
        assert call_kwargs.kwargs["temperature"] == 0.7

    def test_chat_empty_content_returns_empty(self, adapter):
        adapter.client.chat.completions.create.return_value = make_mock_response(
            content=None
        )
        assert adapter.chat([{"role": "user", "content": "x"}]) == ""

    def test_chat_raises_model_call_error(self, adapter):
        adapter.client.chat.completions.create.side_effect = Exception("timeout")
        with pytest.raises(ModelCallError, match="模型调用失败"):
            adapter.chat([{"role": "user", "content": "x"}])


# ============================================================
# chat_stream
# ============================================================

class TestChatStream:
    def test_stream_yields_chunks(self, adapter):
        # 构造流式响应的 chunk 序列
        def make_chunk(text):
            delta = MagicMock()
            delta.content = text
            choice = MagicMock()
            choice.delta = delta
            chunk = MagicMock()
            chunk.choices = [choice]
            return chunk

        adapter.client.chat.completions.create.return_value = iter([
            make_chunk("银"),
            make_chunk("河"),
            make_chunk("平台"),
        ])
        result = list(adapter.chat_stream([{"role": "user", "content": "x"}]))
        assert result == ["银", "河", "平台"]

    def test_stream_skips_empty_delta(self, adapter):
        delta = MagicMock()
        delta.content = None
        choice = MagicMock()
        choice.delta = delta
        chunk = MagicMock()
        chunk.choices = [choice]
        adapter.client.chat.completions.create.return_value = iter([chunk])
        assert list(adapter.chat_stream([{"role": "user", "content": "x"}])) == []

    def test_stream_raises_model_call_error(self, adapter):
        adapter.client.chat.completions.create.side_effect = Exception("connection refused")
        with pytest.raises(ModelCallError, match="流式调用失败"):
            list(adapter.chat_stream([{"role": "user", "content": "x"}]))


# ============================================================
# chat_with_tools
# ============================================================

class TestChatWithTools:
    def test_with_tools_returns_content_and_tool_calls(self, adapter):
        mock_tc = make_mock_tool_call()
        adapter.client.chat.completions.create.return_value = make_mock_response(
            content="",
            tool_calls=[mock_tc],
            finish_reason="tool_calls",
        )
        tools = [{"type": "function", "function": {"name": "check_disk"}}]
        resp = adapter.chat_with_tools(
            [{"role": "user", "content": "检查磁盘"}], tools
        )
        assert isinstance(resp, ChatResponse)
        assert resp.content == ""
        assert len(resp.tool_calls) == 1
        assert isinstance(resp.tool_calls[0], ToolCall)
        assert resp.tool_calls[0].name == "check_disk"
        assert resp.tool_calls[0].arguments == '{"path":"/"}'
        assert resp.finish_reason == "tool_calls"

    def test_with_tools_no_tool_calls(self, adapter):
        adapter.client.chat.completions.create.return_value = make_mock_response(
            content="我没有可调用的工具",
            tool_calls=None,
        )
        resp = adapter.chat_with_tools([{"role": "user", "content": "x"}], [])
        assert resp.content == "我没有可调用的工具"
        assert resp.tool_calls == []

    def test_with_tools_passes_tools_param(self, adapter):
        adapter.client.chat.completions.create.return_value = make_mock_response()
        tools = [{"type": "function", "function": {"name": "f"}}]
        adapter.chat_with_tools([{"role": "user", "content": "x"}], tools)
        call_kwargs = adapter.client.chat.completions.create.call_args
        assert call_kwargs.kwargs["tools"] == tools

    def test_with_tools_raises_model_call_error(self, adapter):
        adapter.client.chat.completions.create.side_effect = Exception("404 not found")
        with pytest.raises(ModelCallError, match="工具调用失败"):
            adapter.chat_with_tools([{"role": "user", "content": "x"}], [])


# ============================================================
# 错误提示分类
# ============================================================

class TestErrorHints:
    def test_timeout_hint(self, adapter):
        adapter.client.chat.completions.create.side_effect = Exception("timed out")
        with pytest.raises(ModelCallError) as exc_info:
            adapter.chat([{"role": "user", "content": "x"}])
        assert "超时" in str(exc_info.value) or "timeout" in str(exc_info.value).lower()

    def test_connection_hint(self, adapter):
        adapter.client.chat.completions.create.side_effect = Exception("connection refused")
        with pytest.raises(ModelCallError) as exc_info:
            adapter.chat([{"role": "user", "content": "x"}])
        assert "连接" in str(exc_info.value) or "connect" in str(exc_info.value).lower()

    def test_model_not_found_hint(self, adapter):
        adapter.client.chat.completions.create.side_effect = Exception("404 model not found")
        with pytest.raises(ModelCallError) as exc_info:
            adapter.chat([{"role": "user", "content": "x"}])
        assert "不存在" in str(exc_info.value) or "not found" in str(exc_info.value).lower()


# ============================================================
# embed
# ============================================================
class TestEmbed:
    def test_embed_returns_vectors(self, adapter):
        data1 = MagicMock(); data1.embedding = [0.1, 0.2, 0.3]
        data2 = MagicMock(); data2.embedding = [0.4, 0.5, 0.6]
        resp = MagicMock(); resp.data = [data1, data2]
        adapter.client.embeddings.create.return_value = resp
        adapter.config.embed_model = "nomic-embed-text"
        vecs = adapter.embed(["hello", "world"])
        assert vecs == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
        adapter.client.embeddings.create.assert_called_once()

    def test_embed_passes_embed_model(self, adapter):
        resp = MagicMock(); resp.data = [MagicMock(embedding=[0.0])]
        adapter.client.embeddings.create.return_value = resp
        adapter.config.embed_model = "bge-m3"
        adapter.embed(["x"])
        kwargs = adapter.client.embeddings.create.call_args.kwargs
        assert kwargs["model"] == "bge-m3"

    def test_embed_explicit_model_overrides_config(self, adapter):
        resp = MagicMock(); resp.data = [MagicMock(embedding=[0.0])]
        adapter.client.embeddings.create.return_value = resp
        adapter.config.embed_model = "bge-m3"
        adapter.embed(["x"], model="nomic-embed-text")
        assert adapter.client.embeddings.create.call_args.kwargs["model"] == "nomic-embed-text"

    def test_embed_empty_model_raises(self, adapter):
        adapter.config.embed_model = ""
        with pytest.raises(ModelCallError):
            adapter.embed(["x"])

    def test_embed_call_failure_raises(self, adapter):
        adapter.config.embed_model = "nomic-embed-text"
        adapter.client.embeddings.create.side_effect = Exception("boom")
        with pytest.raises(ModelCallError):
            adapter.embed(["x"])


def test_mock_embed_deterministic():
    from galaxy_diag.model.mock_client import MockModelAdapter
    m = MockModelAdapter()
    v1 = m.embed(["网络不通"])
    v2 = m.embed(["网络不通"])
    assert v1 == v2
    assert len(v1) == 1 and len(v1[0]) > 0
