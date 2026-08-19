"""客户知识库子系统（REQ-X-02）

案例导入、语义检索、来源标注。
对应设计文档 docs/RAG_Knowledge_Base_design.md。
"""

from galaxy_diag.knowledge.retriever import retrieve_similar
from galaxy_diag.knowledge.store import KnowledgeStore, kb_dir
from galaxy_diag.knowledge.types import (
    KnowledgeCase,
    KnowledgeRef,
    RetrievalResult,
)

__all__ = [
    "KnowledgeCase",
    "KnowledgeRef",
    "RetrievalResult",
    "KnowledgeStore",
    "kb_dir",
    "retrieve_similar",
]
