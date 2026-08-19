"""语义检索测试"""

import pytest

from galaxy_diag.config.defaults import KnowledgeConfig
from galaxy_diag.knowledge.retriever import retrieve_similar
from galaxy_diag.knowledge.store import KnowledgeStore
from galaxy_diag.knowledge.types import KnowledgeCase
from galaxy_diag.model.mock_client import MockModelAdapter
from galaxy_diag.shared.errors import ModelCallError
from galaxy_diag.shared.types import (
    DiagnosticContext, EnvInfo, EnvironmentType, HardwareInfo,
)


def _ctx(desc="CNI 网络异常导致容器不通"):
    return DiagnosticContext(problem_description=desc)


def _env(env=EnvironmentType.CONTAINER):
    return EnvInfo(env_type=env, hardware=HardwareInfo())


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("GALAXY_KB_DIR", str(tmp_path / "kb"))
    s = KnowledgeStore.load()
    adapter = MockModelAdapter()
    for content in ["CNI 网络异常导致容器不通", "NFS 存储挂载失效", "kubelet 服务未运行"]:
        from galaxy_diag.knowledge.indexer import index_file
        import tempfile, os
        f = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8")
        f.write(content); f.close()
        index_file(s, adapter, f.name)
        os.unlink(f.name)
    return s


def test_retrieve_returns_sorted_topk(store):
    adapter = MockModelAdapter()
    result = retrieve_similar(_ctx(), _env(), adapter, store, KnowledgeConfig(top_k=2))
    assert len(result.matches) <= 2
    # 相似度降序
    sims = [s for _, s in result.matches]
    assert sims == sorted(sims, reverse=True)
    # 完全相同文本应最高相似度
    assert result.matches[0][1] == pytest.approx(1.0, abs=1e-6)


def test_retrieve_env_filter(store):
    # 注入一个 container 专属案例
    case = KnowledgeCase(
        case_id="kb_container_only", content="CNI 网络异常导致容器不通",
        content_digest="d", env_type=EnvironmentType.CONTAINER,
    )
    store.add(case, MockModelAdapter().embed(["CNI 网络异常导致容器不通"])[0])
    adapter = MockModelAdapter()
    # VM 环境不应命中 container 专属案例
    result = retrieve_similar(_ctx(), _env(EnvironmentType.VM), adapter, store, KnowledgeConfig(top_k=10))
    ids = {c.case_id for c, _ in result.matches}
    assert "kb_container_only" not in ids


def test_retrieve_empty_store(tmp_path, monkeypatch):
    monkeypatch.setenv("GALAXY_KB_DIR", str(tmp_path / "kb"))
    store = KnowledgeStore.load()
    adapter = MockModelAdapter()
    result = retrieve_similar(_ctx(), _env(), adapter, store, KnowledgeConfig())
    assert result.matches == []


def test_retrieve_threshold_filters(store):
    adapter = MockModelAdapter()
    # 极高阈值，只保留完全匹配
    result = retrieve_similar(_ctx(), _env(), adapter, store,
                              KnowledgeConfig(top_k=10, min_similarity=0.999))
    sims = [s for _, s in result.matches]
    assert all(s >= 0.999 for s in sims)


def test_retrieve_embed_failure_degrades(tmp_path, monkeypatch):
    monkeypatch.setenv("GALAXY_KB_DIR", str(tmp_path / "kb"))
    store = KnowledgeStore.load()
    case = KnowledgeCase(case_id="kb_1", content="CNI 异常", content_digest="d")
    store.add(case, MockModelAdapter().embed(["CNI 异常"])[0])

    class FailAdapter:
        def embed(self, texts, model=None):
            raise ModelCallError("embedding 不可用")

    ctx = _ctx()
    result = retrieve_similar(ctx, _env(), FailAdapter(), store, KnowledgeConfig())
    assert result.matches == []
    assert any("知识库" in w for w in ctx.collection_warnings)
