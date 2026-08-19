"""导入与索引测试"""

import pytest

from galaxy_diag.knowledge.indexer import (
    parse_frontmatter, generate_case_id, index_file, reindex_all,
)
from galaxy_diag.knowledge.store import KnowledgeStore
from galaxy_diag.knowledge.types import KnowledgeCase
from galaxy_diag.model.mock_client import MockModelAdapter


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("GALAXY_KB_DIR", str(tmp_path / "kb"))
    return KnowledgeStore.load()


@pytest.fixture
def adapter():
    return MockModelAdapter()


def test_parse_frontmatter_with_meta():
    text = "---\nenv_type: container\ntags: [net, cni]\n---\n# 标题\n正文"
    meta, body = parse_frontmatter(text)
    assert meta["env_type"] == "container"
    assert meta["tags"] == ["net", "cni"]
    assert "正文" in body


def test_parse_frontmatter_without_meta():
    text = "# 标题\n纯文本无 frontmatter"
    meta, body = parse_frontmatter(text)
    assert meta == {}
    assert "纯文本" in body


def test_generate_case_id_deterministic():
    cid1 = generate_case_id("CNI 异常")
    cid2 = generate_case_id("CNI 异常")
    assert cid1 == cid2
    assert cid1.startswith("kb_")


def test_index_file_imports(store, adapter, tmp_path):
    f = tmp_path / "case1.md"
    f.write_text("---\nenv_type: container\ntags: [net]\n---\nCNI 异常导致网络不通", encoding="utf-8")
    cid = index_file(store, adapter, str(f))
    assert cid.startswith("kb_")
    case = store.get(cid)
    assert case is not None
    assert case.content.startswith("CNI 异常")
    assert case.env_type.value == "container"
    assert store.vector_of(cid) is not None


def test_index_file_dedup_same_content(store, adapter, tmp_path):
    f = tmp_path / "case1.md"
    f.write_text("CNI 异常内容", encoding="utf-8")
    cid1 = index_file(store, adapter, str(f))
    store.save()
    store2 = KnowledgeStore.load()
    cid2 = index_file(store2, adapter, str(f))
    # 相同内容应跳过，返回已有 case_id
    assert cid1 == cid2
    assert len(store2.list_cases()) == 1


def test_reindex_all(store, adapter, tmp_path):
    f1 = tmp_path / "a.md"; f1.write_text("内容A", encoding="utf-8")
    f2 = tmp_path / "b.md"; f2.write_text("内容B", encoding="utf-8")
    index_file(store, adapter, str(f1))
    index_file(store, adapter, str(f2))
    store.save()
    store2 = KnowledgeStore.load()
    n = reindex_all(store2, adapter)
    assert n == 2
    # 重算后向量仍存在
    cases = store2.list_cases()
    assert all(store2.vector_of(c.case_id) for c in cases)
