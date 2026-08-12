"""诊断分析顶层入口测试"""

import json
from unittest.mock import MagicMock, patch

import pytest

from galaxy_diag.diagnoser.agent import diagnose
from galaxy_diag.shared.errors import DiagnoseError, ModelCallError
from galaxy_diag.shared.types import (
    Confidence,
    DiagnosisSource,
    DiagnosticContext,
    EnvInfo,
    EnvironmentType,
    HardwareInfo,
)


def _make_env_info() -> EnvInfo:
    return EnvInfo(
        env_type="bare_metal",
        hardware=HardwareInfo(
            cpu_model="Intel",
            cpu_cores=4,
            memory_total_gb=8.0,
            disks=[],
            raid_cards=[],
            nics=[],
        ),
        storage=[],
    )


def _make_ctx(problem_description: str = "test") -> DiagnosticContext:
    return DiagnosticContext(
        problem_description=problem_description,
        env_info_ref="bare_metal",
    )


def _make_ctx_matching_rule() -> DiagnosticContext:
    """构造一个能命中 resource_oom 规则的上下文（AND 逻辑：需同时包含两个关键词）"""
    from galaxy_diag.shared.types import LogSnippet

    return DiagnosticContext(
        problem_description="系统内存不足 OOM",
        env_info_ref="bare_metal",
        log_snippets=[
            LogSnippet(source="dmesg", level="ERROR", content="Out of memory OOM killed process"),
        ],
    )


class TestDiagnoseRuleMatch:
    def test_rule_match_returns_without_calling_llm(self):
        """规则匹配命中时不调用 LLM"""
        ctx = _make_ctx_matching_rule()
        env_info = _make_env_info()
        mock_adapter = MagicMock()

        result = diagnose("内存不足", env_info, ctx, mock_adapter)

        assert result.diagnosis_source == DiagnosisSource.RULE_MATCH
        assert result.confidence == Confidence.CONFIRMED
        mock_adapter.chat.assert_not_called()


class TestDiagnoseLLMPath:
    def test_llm_success_returns_result(self):
        """规则未命中 + LLM 返回有效 JSON"""
        ctx = _make_ctx("something unrelated xyz")
        env_info = _make_env_info()
        mock_adapter = MagicMock()

        llm_output = json.dumps({
            "root_cause": "test cause",
            "confidence": "suspected",
            "evidence": ["e1"],
            "missing_info": [],
            "investigation_steps": ["step1"],
            "fault_scope": "scope",
        })
        mock_adapter.chat.return_value = llm_output

        result = diagnose("unrelated problem", env_info, ctx, mock_adapter)

        assert result.root_cause == "test cause"
        assert result.confidence == Confidence.SUSPECTED
        mock_adapter.chat.assert_called_once()


class TestDiagnoseModelCallError:
    def test_model_call_error_returns_error_fallback(self):
        """LLM 调用失败 → ERROR_FALLBACK"""
        ctx = _make_ctx("test")
        env_info = _make_env_info()
        mock_adapter = MagicMock()
        mock_adapter.chat.side_effect = ModelCallError("LLM 超时")

        result = diagnose("test", env_info, ctx, mock_adapter)

        assert result.diagnosis_source == DiagnosisSource.ERROR_FALLBACK
        assert result.confidence == Confidence.INSUFFICIENT


class TestDiagnoseJSONParseRetry:
    def test_invalid_json_then_valid_on_retry(self):
        """第一次 JSON 解析失败，重试成功"""
        ctx = _make_ctx("test")
        env_info = _make_env_info()
        mock_adapter = MagicMock()

        # 第一次返回无效 JSON，第二次返回有效 JSON
        valid_output = json.dumps({
            "root_cause": "retry cause",
            "confidence": "suspected",
            "evidence": ["e1"],
            "missing_info": [],
            "investigation_steps": [],
            "fault_scope": "",
        })
        mock_adapter.chat.side_effect = ["不是 JSON", valid_output]

        result = diagnose("test", env_info, ctx, mock_adapter)

        assert result.root_cause == "retry cause"
        assert mock_adapter.chat.call_count == 2


class TestDiagnoseJSONParseRetryFails:
    def test_both_calls_invalid_returns_error_fallback(self):
        """两次 JSON 解析都失败 → ERROR_FALLBACK"""
        ctx = _make_ctx("test")
        env_info = _make_env_info()
        mock_adapter = MagicMock()
        mock_adapter.chat.side_effect = ["不是 JSON", "还是不是 JSON"]

        result = diagnose("test", env_info, ctx, mock_adapter)

        assert result.diagnosis_source == DiagnosisSource.ERROR_FALLBACK
        assert result.confidence == Confidence.INSUFFICIENT
