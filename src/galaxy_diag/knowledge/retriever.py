"""语义检索（REQ-X-02）

查询构造 → 环境过滤 → 余弦相似度 top-k → 阈值过滤。
检索是纯函数，由 diagnoser/agent.py 调用（依赖方向：diagnoser → knowledge）。
embedding 失败时降级返回空结果并记入 collection_warnings（对齐采集降级）。
对应设计文档 §检索流程设计。
"""

from __future__ import annotations

import numpy as np

from galaxy_diag.config.defaults import KnowledgeConfig
from galaxy_diag.diagnoser.rules import _concat_context_text
from galaxy_diag.knowledge.store import KnowledgeStore
from galaxy_diag.knowledge.types import RetrievalResult
from galaxy_diag.shared.errors import ModelCallError
from galaxy_diag.shared.types import DiagnosticContext, EnvInfo


def _cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度（向量已归一化时等价于点积；此处通用实现）"""
    va = np.array(a, dtype=float)
    vb = np.array(b, dtype=float)
    denom = (np.linalg.norm(va) * np.linalg.norm(vb)) or 1.0
    return float(np.dot(va, vb) / denom)


def retrieve_similar(
    ctx: DiagnosticContext,
    env_info: EnvInfo,
    model_adapter,
    store: KnowledgeStore,
    knowledge_config: KnowledgeConfig,
) -> RetrievalResult:
    """检索语义相似的客户案例

    Args:
        ctx: 诊断上下文（用于构造查询文本；降级时写入 collection_warnings）
        env_info: 环境信息（用于环境过滤）
        model_adapter: 提供 embed()
        store: 知识库存储
        knowledge_config: top_k / min_similarity

    Returns:
        RetrievalResult：matches 按相似度降序，可能为空（空库/降级/无相似）
    """
    query_text = _concat_context_text(ctx)

    # 空库或维度不一致：跳过
    cases = store.list_cases()
    if not cases or not store.is_dimension_consistent():
        return RetrievalResult(matches=[], query=query_text)

    # 环境过滤：env_type 非空的案例按环境过滤；None 案例全环境适用
    env_type = env_info.env_type
    candidates = [
        c for c in cases
        if c.env_type is None or c.env_type == env_type
    ]
    if not candidates:
        return RetrievalResult(matches=[], query=query_text)

    # 查询向量化（降级：embedding 失败 → 跳过 RAG）
    try:
        query_vec = model_adapter.embed([query_text])[0]
    except ModelCallError:
        ctx.collection_warnings.append(
            "客户知识库检索不可用（embedding 服务异常），已跳过"
        )
        return RetrievalResult(matches=[], query=query_text)

    # 余弦相似度排序
    scored = [(c, _cosine(query_vec, store.vector_of(c.case_id))) for c in candidates]
    scored.sort(key=lambda x: x[1], reverse=True)

    # 阈值过滤 + top-k
    matches = [
        (c, s) for c, s in scored
        if s >= knowledge_config.min_similarity
    ][:knowledge_config.top_k]

    return RetrievalResult(matches=matches, query=query_text)
