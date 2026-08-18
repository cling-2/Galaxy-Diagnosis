"""修复生成失败反馈回灌测试（Cause A）

验证 generate() 在传入 prior_failures 时，反馈文本会进入发送给 LLM 的消息。
"""

import json
from unittest.mock import MagicMock

from galaxy_diag.fixer.agent import generate
from galaxy_diag.fixer.prompts import build_fix_messages
from galaxy_diag.shared.types import (
    Confidence,
    DiagnosisResult,
    DiagnosisSource,
    EnvInfo,
    EnvironmentType,
    HardwareInfo,
)


_VALID_FIX_JSON = json.dumps({
    "steps": [
        {
            "command": "systemctl status galaxy-api",
            "description": "验证服务状态",
            "risk_note": "只读操作，无风险",
            "parameters": {},
            "is_verification": True,
        },
    ],
    "script_language": "bash",
    "risk_notes": ["无额外风险"],
    "impact_scope": "仅查看状态",
}, ensure_ascii=False)


def _make_env_info() -> EnvInfo:
    return EnvInfo(
        env_type=EnvironmentType.CONTAINER,
        hardware=HardwareInfo(
            cpu_model="Intel",
            cpu_cores=4,
            memory_total_gb=8.0,
            disks=[],
            raid_cards=[],
            nics=[],
        ),
        storage=[],
        has_docker_cli=False,
        has_kubectl_cli=False,
    )


def _make_diagnosis() -> DiagnosisResult:
    return DiagnosisResult(
        root_cause="galaxy-api 服务异常",
        confidence=Confidence.CONFIRMED,
        evidence=["systemctl status 显示 failed"],
        missing_info=[],
        env_type=EnvironmentType.CONTAINER,
        investigation_steps=[],
        fault_scope="服务层",
        diagnosis_source=DiagnosisSource.LLM,
    )


class _CapturingAdapter:
    """捕获发送给 LLM 的消息，返回合法 fix JSON"""

    def __init__(self):
        self.captured_messages: list[list[dict]] = []

    def chat(self, messages, **kwargs):
        self.captured_messages.append([dict(m) for m in messages])
        return _VALID_FIX_JSON


class TestGenerateFeedback:
    def test_generate_without_failures(self):
        """无 prior_failures 时，消息中不应含反馈节"""
        adapter = _CapturingAdapter()
        generate(
            diagnosis=_make_diagnosis(),
            env_info=_make_env_info(),
            model_adapter=adapter,
            prior_failures=None,
        )
        assert len(adapter.captured_messages) == 1
        last_user = [m for m in adapter.captured_messages[0] if m["role"] == "user"][-1]
        assert "上次生成未通过检测" not in last_user["content"]

    def test_generate_with_failures_includes_feedback(self):
        """prior_failures 非空时，反馈文本应进入最后一条 user 消息"""
        adapter = _CapturingAdapter()
        failures = ["步骤 1 含未替换的占位符: <FOO>"]
        generate(
            diagnosis=_make_diagnosis(),
            env_info=_make_env_info(),
            model_adapter=adapter,
            prior_failures=failures,
        )
        assert len(adapter.captured_messages) == 1
        last_user = [m for m in adapter.captured_messages[0] if m["role"] == "user"][-1]
        assert "上次生成未通过检测" in last_user["content"]
        assert "<FOO>" in last_user["content"]
        assert "prior-failures" in last_user["content"]


class TestBuildFixMessagesFeedback:
    def test_build_messages_without_failures(self):
        """build_fix_messages 默认不含反馈节"""
        messages = build_fix_messages(_make_diagnosis(), _make_env_info())
        last_user = [m for m in messages if m["role"] == "user"][-1]
        assert "上次生成未通过检测" not in last_user["content"]

    def test_build_messages_with_failures(self):
        """build_fix_messages 传入 failures 后含反馈节"""
        messages = build_fix_messages(
            _make_diagnosis(), _make_env_info(),
            prior_failures=["某步骤不兼容"],
        )
        last_user = [m for m in messages if m["role"] == "user"][-1]
        assert "上次生成未通过检测" in last_user["content"]
        assert "某步骤不兼容" in last_user["content"]
