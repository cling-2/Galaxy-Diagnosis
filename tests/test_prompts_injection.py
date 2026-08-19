"""Prompt 注入客户案例段测试"""

from galaxy_diag.diagnoser.prompts import (
    format_diagnosis_context, build_diagnosis_messages,
    SYSTEM_PROMPT,
)
from galaxy_diag.knowledge.types import KnowledgeCase, RetrievalResult
from galaxy_diag.shared.types import (
    DiagnosticContext, EnvInfo, EnvironmentType, HardwareInfo,
)


def _ctx():
    return DiagnosticContext(problem_description="网络不通")


def _env():
    return EnvInfo(env_type=EnvironmentType.CONTAINER, hardware=HardwareInfo())


def _retrieval():
    case = KnowledgeCase(
        case_id="kb_1", content="CNI 插件异常导致 Pod 无法通信",
        content_digest="d", env_type=EnvironmentType.CONTAINER,
    )
    return RetrievalResult(matches=[(case, 0.82)], query="网络不通")


def test_context_without_retrieval_unchanged():
    """无检索结果时上下文不含客户案例段（行为零退化）"""
    text = format_diagnosis_context(_ctx(), _env())
    assert "客户案例" not in text


def test_context_with_retrieval_injects_cases():
    text = format_diagnosis_context(_ctx(), _env(), _retrieval())
    assert "客户案例" in text
    assert "<customer-cases>" in text
    assert "CNI 插件异常" in text
    assert "0.82" in text


def test_context_with_empty_retrieval_no_injection():
    text = format_diagnosis_context(
        _ctx(), _env(), RetrievalResult(matches=[], query="q")
    )
    assert "客户案例" not in text


def test_messages_include_system_rule_for_customer_cases():
    """System Prompt 含客户案例防注入规则"""
    assert "customer-cases" in SYSTEM_PROMPT


def test_build_messages_passes_retrieval():
    msgs = build_diagnosis_messages("网络不通", _env(), _ctx(), _retrieval())
    user_content = msgs[-1]["content"]
    assert "<customer-cases>" in user_content


def test_build_messages_without_retrieval_unchanged():
    """无检索结果时 build_diagnosis_messages 输出不含客户案例段"""
    msgs = build_diagnosis_messages("网络不通", _env(), _ctx())
    user_content = msgs[-1]["content"]
    assert "客户案例" not in user_content
