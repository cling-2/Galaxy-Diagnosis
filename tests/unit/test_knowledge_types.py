"""knowledge 数据类测试"""

from galaxy_diag.knowledge.types import KnowledgeCase, KnowledgeRef, RetrievalResult
from galaxy_diag.shared.types import EnvironmentType


def test_knowledge_case_defaults():
    c = KnowledgeCase(case_id="kb_1", content="现象...", content_digest="d1")
    assert c.env_type is None
    assert c.tags == []
    assert c.case_id == "kb_1"


def test_knowledge_case_with_env():
    c = KnowledgeCase(
        case_id="kb_2", content="c", env_type=EnvironmentType.CONTAINER,
        tags=["net"], content_digest="d2",
    )
    assert c.env_type == EnvironmentType.CONTAINER
    assert c.tags == ["net"]


def test_knowledge_ref():
    r = KnowledgeRef(case_id="kb_1", similarity=0.82, summary="CNI 异常")
    assert r.similarity == 0.82


def test_retrieval_result_empty():
    r = RetrievalResult(matches=[], query="net down")
    assert r.matches == []
    assert r.query == "net down"


def test_retrieval_result_with_matches():
    c = KnowledgeCase(case_id="kb_1", content="c", content_digest="d")
    r = RetrievalResult(matches=[(c, 0.9)], query="q")
    assert len(r.matches) == 1
    assert r.matches[0][1] == 0.9
