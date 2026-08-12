"""Prompt 模板测试"""

import pytest

from galaxy_diag.diagnoser.prompts import (
    SYSTEM_PROMPT,
    FEW_SHOT_EXAMPLES,
    build_diagnosis_messages,
    format_diagnosis_context,
)
from galaxy_diag.shared.types import (
    DiagnosticContext,
    EnvInfo,
    HardwareInfo,
    LogSnippet,
    StorageInfo,
)


def _make_env_info() -> EnvInfo:
    return EnvInfo(
        env_type="bare_metal",
        hardware=HardwareInfo(
            cpu_model="Intel Xeon",
            cpu_cores=8,
            memory_total_gb=32.0,
            disks=[],
            raid_cards=[],
            nics=[],
        ),
        storage=[],
    )


def _make_ctx(
    problem_description: str = "test problem",
    log_content: str = "",
    user_provided: list[str] | None = None,
    collection_warnings: list[str] | None = None,
) -> DiagnosticContext:
    snippets = []
    if log_content:
        snippets = [LogSnippet(source="test.log", level="ERROR", content=log_content)]
    return DiagnosticContext(
        problem_description=problem_description,
        env_info_ref="bare_metal",
        log_snippets=snippets,
        user_provided=user_provided or [],
        collection_warnings=collection_warnings or [],
    )


class TestSystemPrompt:
    def test_contains_json_schema(self):
        assert "root_cause" in SYSTEM_PROMPT
        assert "confidence" in SYSTEM_PROMPT
        assert "confirmed" in SYSTEM_PROMPT
        assert "insufficient" in SYSTEM_PROMPT

    def test_contains_rules(self):
        assert "evidence" in SYSTEM_PROMPT
        assert "user-input" in SYSTEM_PROMPT


class TestFewShotExamples:
    def test_count(self):
        # 3 pairs (user+assistant) = 6 messages
        assert len(FEW_SHOT_EXAMPLES) == 6

    def test_all_have_role_and_content(self):
        for msg in FEW_SHOT_EXAMPLES:
            assert "role" in msg
            assert "content" in msg
            assert msg["role"] in ("user", "assistant")


class TestFormatDiagnosisContext:
    def test_user_description_wrapped(self):
        ctx = _make_ctx(problem_description="网络不通")
        env_info = _make_env_info()
        text = format_diagnosis_context(ctx, env_info)
        assert "<user-input>" in text
        assert "网络不通" in text
        assert "</user-input>" in text

    def test_log_snippets_wrapped(self):
        ctx = _make_ctx(log_content="error: disk failed")
        env_info = _make_env_info()
        text = format_diagnosis_context(ctx, env_info)
        assert "<log" in text
        assert "error: disk failed" in text
        assert "</log>" in text

    def test_user_provided_wrapped(self):
        ctx = _make_ctx(user_provided=["user uploaded log content"])
        env_info = _make_env_info()
        text = format_diagnosis_context(ctx, env_info)
        assert "<user-log>" in text
        assert "user uploaded log content" in text
        assert "</user-log>" in text

    def test_empty_sections_omitted(self):
        ctx = _make_ctx()
        env_info = _make_env_info()
        text = format_diagnosis_context(ctx, env_info)
        assert "## 组件状态" not in text
        assert "## 日志" not in text

    def test_collection_warnings_rendered(self):
        ctx = _make_ctx(collection_warnings=["systemctl not found"])
        env_info = _make_env_info()
        text = format_diagnosis_context(ctx, env_info)
        assert "## 采集受限" in text
        assert "systemctl not found" in text

    def test_env_info_present(self):
        ctx = _make_ctx()
        env_info = _make_env_info()
        text = format_diagnosis_context(ctx, env_info)
        assert "Intel Xeon" in text
        assert "32.0" in text


class TestBuildDiagnosisMessages:
    def test_returns_list_of_dicts(self):
        ctx = _make_ctx()
        env_info = _make_env_info()
        messages = build_diagnosis_messages("test", env_info, ctx)
        assert isinstance(messages, list)
        assert all(isinstance(m, dict) for m in messages)

    def test_first_message_is_system(self):
        ctx = _make_ctx()
        env_info = _make_env_info()
        messages = build_diagnosis_messages("test", env_info, ctx)
        assert messages[0]["role"] == "system"
        assert "银河平台" in messages[0]["content"]

    def test_last_message_is_user(self):
        ctx = _make_ctx()
        env_info = _make_env_info()
        messages = build_diagnosis_messages("test", env_info, ctx)
        assert messages[-1]["role"] == "user"
