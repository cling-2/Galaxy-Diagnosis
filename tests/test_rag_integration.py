"""diagnose() RAG 集成测试（mock embedding + mock LLM）

验证 diagnose() 接入 RAG 检索增强后的端到端行为：
1. 不传 kb_store 时行为与原有一致
2. 传入 kb_store + knowledge_config 时填充 referenced_knowledge
3. 规则命中时跳过 RAG（referenced_knowledge 为空）
4. embedding 失败时降级走纯 LLM，referenced_knowledge 为空
"""

import os
import tempfile

import pytest

from galaxy_diag.config.defaults import KnowledgeConfig, LLMConfig
from galaxy_diag.diagnoser import diagnose
from galaxy_diag.knowledge.indexer import index_file
from galaxy_diag.knowledge.store import KnowledgeStore
from galaxy_diag.model.mock_client import MockModelAdapter
from galaxy_diag.shared.types import (
    DiagnosticContext,
    EnvInfo,
    EnvironmentType,
    HardwareInfo,
    DiagnosisSource,
)


def _mock_adapter() -> MockModelAdapter:
    """MockModelAdapter with embed_model configured (enables RAG trigger)."""
    return MockModelAdapter(config=LLMConfig(embed_model="mock-embed"))


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("GALAXY_KB_DIR", str(tmp_path / "kb"))
    s = KnowledgeStore.load()
    adapter = MockModelAdapter()
    f = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8")
    # 内容与查询文本完全一致，保证 cosine similarity = 1.0
    # （MockModelAdapter 的 embed 基于 SHA256，不同文本向量随机，
    #   相似度不可预测；用相同文本确保测试可复现）
    f.write("CNI 网络异常导致容器不通")
    f.close()
    index_file(s, adapter, f.name)
    os.unlink(f.name)
    return s


def _ctx(desc="CNI 网络异常导致容器不通"):
    return DiagnosticContext(
        problem_description=desc, env_info_ref=EnvironmentType.CONTAINER
    )


def _env():
    return EnvInfo(env_type=EnvironmentType.CONTAINER, hardware=HardwareInfo())


def test_diagnose_without_kb_unchanged():
    """不传 kb_store 时行为与原有一致（referenced_knowledge 为空）"""
    result = diagnose("某故障", _env(), _ctx("某未知故障不命中规则"), MockModelAdapter())
    assert result.referenced_knowledge == []


def test_diagnose_with_kb_fills_referenced_knowledge(store):
    """传入 kb_store + knowledge_config 时填充 referenced_knowledge"""
    result = diagnose(
        "CNI 网络异常导致容器不通",
        _env(),
        _ctx(),
        _mock_adapter(),
        kb_store=store,
        knowledge_config=KnowledgeConfig(),
    )
    assert len(result.referenced_knowledge) >= 1
    assert result.diagnosis_source == DiagnosisSource.LLM


def test_diagnose_rule_match_skips_rag(store):
    """规则命中时不触发 RAG（referenced_knowledge 为空）"""
    # CrashLoopBackOff 命中 CONFIRMED 规则（需 container 环境）
    ctx = DiagnosticContext(
        problem_description="Pod CrashLoopBackOff",
        env_info_ref=EnvironmentType.CONTAINER,
    )
    result = diagnose(
        "Pod 崩溃",
        _env(),
        ctx,
        _mock_adapter(),
        kb_store=store,
        knowledge_config=KnowledgeConfig(),
    )
    assert result.diagnosis_source == DiagnosisSource.RULE_MATCH
    assert result.referenced_knowledge == []


def test_diagnose_embed_failure_degrades(store, monkeypatch):
    """embedding 失败时降级走纯 LLM，referenced_knowledge 为空"""

    class FailAdapter(MockModelAdapter):
        def embed(self, texts, model=None):
            from galaxy_diag.shared.errors import ModelCallError

            raise ModelCallError("不可用")

    ctx = _ctx()
    result = diagnose(
        "CNI 网络异常导致容器不通",
        _env(),
        ctx,
        FailAdapter(config=LLMConfig(embed_model="mock-embed")),
        kb_store=store,
        knowledge_config=KnowledgeConfig(),
    )
    assert result.referenced_knowledge == []
    assert any("知识库" in w for w in ctx.collection_warnings)
