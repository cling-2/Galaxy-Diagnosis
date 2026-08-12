"""LLM 输出后处理测试"""

import json

import pytest

from galaxy_diag.diagnoser.postprocess import (
    _extract_json,
    _validate_schema,
    _validate_semantic,
    build_error_fallback,
    parse_diagnosis_response,
)
from galaxy_diag.shared.errors import DiagnoseError
from galaxy_diag.shared.types import (
    Confidence,
    DiagnosisSource,
    EnvironmentType,
)


class TestExtractJson:
    def test_valid_json_string(self):
        data = {"root_cause": "test", "confidence": "confirmed"}
        result = _extract_json(json.dumps(data))
        assert result == data

    def test_json_in_markdown_code_block(self):
        data = {"root_cause": "test", "confidence": "suspected"}
        text = f"```json\n{json.dumps(data, ensure_ascii=False)}\n```"
        result = _extract_json(text)
        assert result == data

    def test_json_in_text(self):
        data = {"root_cause": "test", "confidence": "insufficient"}
        text = f"分析结果如下：\n{json.dumps(data)}\n以上是结论。"
        result = _extract_json(text)
        assert result is not None
        assert result["confidence"] == "insufficient"

    def test_no_json_returns_none(self):
        result = _extract_json("这是一段纯文本，没有 JSON")
        assert result is None

    def test_malformed_json_returns_none(self):
        result = _extract_json("{invalid json")
        assert result is None


class TestValidateSchema:
    def test_all_fields_present(self):
        data = {
            "root_cause": "test",
            "confidence": "confirmed",
            "evidence": ["e1"],
            "missing_info": [],
            "investigation_steps": [],
            "fault_scope": "",
        }
        result, repaired = _validate_schema(data.copy())
        assert not repaired
        assert result["confidence"] == "confirmed"

    def test_missing_optional_fields_filled(self):
        data = {"root_cause": "test", "confidence": "suspected", "evidence": ["e1"], "missing_info": []}
        result, repaired = _validate_schema(data)
        assert repaired
        assert result["investigation_steps"] == []
        assert result["fault_scope"] == ""

    def test_invalid_confidence_repaired(self):
        data = {"root_cause": "test", "confidence": "invalid_value", "evidence": [], "missing_info": []}
        result, repaired = _validate_schema(data)
        assert repaired
        assert result["confidence"] == "suspected"  # 保守降级


class TestValidateSemantic:
    def test_confirmed_without_evidence_repaired(self):
        data = {"confidence": "confirmed", "root_cause": "test", "evidence": [], "missing_info": []}
        result, repaired = _validate_semantic(data)
        assert repaired
        assert result["confidence"] == "suspected"  # 降级

    def test_suspected_without_evidence_repaired(self):
        data = {"confidence": "suspected", "root_cause": "test", "evidence": [], "missing_info": []}
        result, repaired = _validate_semantic(data)
        assert repaired
        assert "LLM 未提供证据" in result["evidence"]

    def test_insufficient_without_missing_info_repaired(self):
        data = {"confidence": "insufficient", "root_cause": "", "evidence": [], "missing_info": [], "investigation_steps": []}
        result, repaired = _validate_semantic(data)
        assert repaired
        assert len(result["missing_info"]) > 0
        assert len(result["investigation_steps"]) > 0

    def test_valid_confirmed_passes(self):
        data = {
            "confidence": "confirmed",
            "root_cause": "NFS 失效",
            "evidence": ["日志中发现 stale file handle"],
            "missing_info": [],
            "investigation_steps": [],
        }
        result, repaired = _validate_semantic(data)
        assert not repaired


class TestParseDiagnosisResponse:
    def test_valid_json_returns_llm_source(self):
        data = {
            "root_cause": "test cause",
            "confidence": "confirmed",
            "evidence": ["e1"],
            "missing_info": [],
            "investigation_steps": [],
            "fault_scope": "scope",
        }
        result = parse_diagnosis_response(json.dumps(data), EnvironmentType.BARE_METAL)
        assert result.diagnosis_source == DiagnosisSource.LLM
        assert result.root_cause == "test cause"
        assert result.confidence == Confidence.CONFIRMED

    def test_needs_repair_returns_fallback_source(self):
        data = {
            "root_cause": "test",
            "confidence": "confirmed",
            "evidence": [],  # confirmed 但没 evidence → 需修复
            "missing_info": [],
        }
        result = parse_diagnosis_response(json.dumps(data), EnvironmentType.VM)
        assert result.diagnosis_source == DiagnosisSource.LLM_FALLBACK
        assert result.confidence == Confidence.SUSPECTED  # 降级

    def test_invalid_json_raises_diagnose_error(self):
        with pytest.raises(DiagnoseError):
            parse_diagnosis_response("这不是 JSON", EnvironmentType.CONTAINER)

    def test_env_type_passed_through(self):
        data = {"root_cause": "t", "confidence": "suspected", "evidence": ["e"], "missing_info": []}
        result = parse_diagnosis_response(json.dumps(data), EnvironmentType.CONTAINER)
        assert result.env_type == EnvironmentType.CONTAINER


class TestBuildErrorFallback:
    def test_returns_error_fallback_source(self):
        result = build_error_fallback(EnvironmentType.VM, "LLM 超时")
        assert result.diagnosis_source == DiagnosisSource.ERROR_FALLBACK
        assert result.confidence == Confidence.INSUFFICIENT
        assert "LLM 超时" in result.missing_info
        assert len(result.investigation_steps) > 0
