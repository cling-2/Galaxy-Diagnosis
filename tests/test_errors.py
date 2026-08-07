"""错误类测试

验证 GalaxyDiagError 及子类的 message/hint/format 行为。
"""

import pytest

from galaxy_diag.shared.errors import (
    GalaxyDiagError,
    ConfigError,
    PrecheckError,
    ModelUnavailableError,
    ModelCallError,
    CollectorError,
    CollectorPermissionError,
    CollectorPartialError,
    CollectorToolNotFoundError,
)


class TestGalaxyDiagError:
    def test_message_only(self):
        e = GalaxyDiagError("something wrong")
        assert e.message == "something wrong"
        assert e.hint == ""
        assert str(e) == "something wrong"

    def test_message_with_hint(self):
        e = GalaxyDiagError("something wrong", hint="try this")
        assert e.message == "something wrong"
        assert e.hint == "try this"
        assert "try this" in str(e)

    def test_hint_format(self):
        e = GalaxyDiagError("err", hint="do x")
        formatted = e.format()
        assert "err" in formatted
        assert "do x" in formatted
        assert "\U0001f4a1" in formatted  # 💡 emoji


class TestErrorSubclasses:
    def test_config_error(self):
        e = ConfigError("bad yaml", hint="check syntax")
        assert isinstance(e, GalaxyDiagError)
        assert "bad yaml" in str(e)

    def test_precheck_error(self):
        e = PrecheckError("内存不足")
        assert isinstance(e, GalaxyDiagError)

    def test_model_unavailable_error(self):
        e = ModelUnavailableError("服务不可达", hint="启动 Ollama")
        assert isinstance(e, GalaxyDiagError)

    def test_model_call_error(self):
        e = ModelCallError("推理超时", hint="减小模型")
        assert isinstance(e, GalaxyDiagError)

    def test_subclasses_are_distinct(self):
        """不同子类不应相等"""
        e1 = ConfigError("x")
        e2 = ModelCallError("x")
        assert type(e1) != type(e2)


class TestCollectorErrorSubclasses:
    def test_collector_permission_error(self):
        e = CollectorPermissionError("无权限读 DMI", hint="请以 root 运行")
        assert isinstance(e, CollectorError)
        assert isinstance(e, GalaxyDiagError)
        assert "无权限" in str(e)
        assert "root" in str(e)

    def test_collector_partial_error(self):
        e = CollectorPartialError("RAID 采集失败")
        assert isinstance(e, CollectorError)
        assert isinstance(e, GalaxyDiagError)

    def test_collector_tool_not_found_error(self):
        e = CollectorToolNotFoundError("lspci 未安装", hint="apt install pciutils")
        assert isinstance(e, CollectorError)
        assert isinstance(e, GalaxyDiagError)

    def test_collector_subclasses_distinct(self):
        e1 = CollectorPermissionError("x")
        e2 = CollectorToolNotFoundError("x")
        e3 = CollectorPartialError("x")
        types = {type(e1), type(e2), type(e3)}
        assert len(types) == 3
