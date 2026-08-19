"""KnowledgeStore 存储层测试"""

import json

import numpy as np
import pytest

from galaxy_diag.knowledge.store import KnowledgeStore
from galaxy_diag.knowledge.types import KnowledgeCase


@pytest.fixture
def store_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("GALAXY_KB_DIR", str(tmp_path / "kb"))
    return tmp_path / "kb"


def _make_case(cid="kb_1", content="CNI 异常", offset=0):
    return KnowledgeCase(case_id=cid, content=content, content_digest=f"d_{cid}"), [0.1, 0.2, 0.3]


def test_store_empty_when_no_files(store_dir):
    store = KnowledgeStore.load()
    assert store.cases == []
    assert store.vector_dim == 0


def test_store_add_and_get(store_dir):
    store = KnowledgeStore.load()
    case, vec = _make_case()
    store.add(case, vec)
    store.save()
    assert store.get("kb_1") is not None
    assert store.vector_of("kb_1") == pytest.approx([0.1, 0.2, 0.3])
    assert store.vector_dim == 3


def test_store_persist_and_reload(store_dir):
    store = KnowledgeStore.load()
    case, vec = _make_case()
    store.add(case, vec)
    store.save()

    # 重新加载
    store2 = KnowledgeStore.load()
    assert len(store2.cases) == 1
    assert store2.get("kb_1").content == "CNI 异常"
    assert store2.vector_of("kb_1") == pytest.approx([0.1, 0.2, 0.3])


def test_store_delete(store_dir):
    store = KnowledgeStore.load()
    for cid in ["kb_1", "kb_2", "kb_3"]:
        store.add(KnowledgeCase(case_id=cid, content="c", content_digest="d"), [0.1, 0.2, 0.3])
    store.save()
    store.delete("kb_2")
    store.save()
    store2 = KnowledgeStore.load()
    ids = {c.case_id for c in store2.cases}
    assert ids == {"kb_1", "kb_3"}
    assert store2.get("kb_2") is None


def test_store_list_cases(store_dir):
    store = KnowledgeStore.load()
    store.add(KnowledgeCase(case_id="kb_1", content="c1", content_digest="d1"), [0.1, 0.2, 0.3])
    store.add(KnowledgeCase(case_id="kb_2", content="c2", content_digest="d2"), [0.1, 0.2, 0.3])
    cases = store.list_cases()
    assert {c.case_id for c in cases} == {"kb_1", "kb_2"}


def test_store_dim_mismatch_detection(store_dir):
    store = KnowledgeStore.load()
    store.add(KnowledgeCase(case_id="kb_1", content="c", content_digest="d"), [0.1, 0.2, 0.3])
    store.save()
    # 写入不同维度的旧索引文件模拟模型更换
    # 重新加载后检测到 vectors.npy 与 index 维度一致；单独构造维度不一致场景：
    store.add(KnowledgeCase(case_id="kb_2", content="c", content_digest="d"), [0.1, 0.2])  # 维度不同
    store.save()
    store2 = KnowledgeStore.load()
    assert not store2.is_dimension_consistent()


def test_store_files_created(store_dir):
    store = KnowledgeStore.load()
    case, vec = _make_case()
    store.add(case, vec)
    store.save()
    assert (store_dir / "cases" / "kb_1.md").exists()
    assert (store_dir / "index.json").exists()
    assert (store_dir / "vectors.npy").exists()
    idx = json.loads((store_dir / "index.json").read_text(encoding="utf-8"))
    assert "kb_1" in idx
