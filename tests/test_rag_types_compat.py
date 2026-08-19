"""DiagnosisResult 与 KnowledgeRef 兼容性测试（REQ-X-02 / Task 7）"""

import json
from dataclasses import asdict

from galaxy_diag.shared.types import DiagnosisResult, KnowledgeRef


def test_diagnosis_result_has_referenced_knowledge_default():
    r = DiagnosisResult()
    assert r.referenced_knowledge == []


def test_diagnosis_result_with_refs():
    r = DiagnosisResult(referenced_knowledge=[
        KnowledgeRef(case_id="kb_1", similarity=0.8, summary="CNI 异常"),
    ])
    assert len(r.referenced_knowledge) == 1
    assert r.referenced_knowledge[0].case_id == "kb_1"


def test_knowledge_ref_serializable():
    """KnowledgeRef 可被 asdict + json 序列化（persist.py 需要）"""
    r = DiagnosisResult(referenced_knowledge=[
        KnowledgeRef(case_id="kb_1", similarity=0.8, summary="x"),
    ])
    d = asdict(r)
    s = json.dumps(d, ensure_ascii=False)
    assert "kb_1" in s


def test_knowledge_ref_shared_between_knowledge_and_shared():
    """knowledge.types 导入的 KnowledgeRef 与 shared.types 的是同一个类"""
    from galaxy_diag.knowledge.types import KnowledgeRef as KRef2
    assert KRef2 is KnowledgeRef
