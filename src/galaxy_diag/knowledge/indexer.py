"""导入与索引（REQ-X-02）

解析 Markdown frontmatter → 整条案例为一个 chunk → embedding → 持久化。
支持增量更新：重复导入相同内容（content_digest 一致）跳过。
对应设计文档 §数据结构设计·嵌入时机。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from galaxy_diag.knowledge.store import KnowledgeStore
from galaxy_diag.knowledge.types import KnowledgeCase
from galaxy_diag.shared.errors import GalaxyDiagError, ModelCallError
from galaxy_diag.shared.types import EnvironmentType

# 借用既有 yaml 依赖解析 frontmatter
import yaml


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """解析 YAML frontmatter，返回 (metadata, body)

    无 frontmatter 时返回 ({}, 原文)。
    frontmatter 格式：文件以 '---' 开头，至下一个 '---' 结束。
    """
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    # parts[0] 为空（开头 ---），parts[1] 为 frontmatter，parts[2] 为正文
    if len(parts) < 3:
        return {}, text
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}, text
    return meta, parts[2].lstrip("\n")


def generate_case_id(content: str) -> str:
    """根据内容生成确定性 case_id（kb_ + 内容 hash 前 12 位）"""
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
    return f"kb_{digest}"


def _content_digest(content: str) -> str:
    """内容摘要（用于增量判定）"""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _build_case(file_path: str) -> KnowledgeCase:
    """读取文件并解析为 KnowledgeCase（不含向量）"""
    text = Path(file_path).read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)
    content = body.strip()
    env_raw = meta.get("env_type")
    env_type = EnvironmentType(env_raw) if env_raw else None
    tags = meta.get("tags") or []
    if not isinstance(tags, list):
        tags = [str(tags)]
    return KnowledgeCase(
        case_id=generate_case_id(content),
        content=content,
        content_digest=_content_digest(content),
        env_type=env_type,
        tags=[str(t) for t in tags],
    )


def index_file(
    store: KnowledgeStore,
    model_adapter,
    file_path: str,
) -> str:
    """导入单个案例文件：解析 → embedding → 持久化

    重复导入相同内容（content_digest 一致）则跳过 embedding，返回已有 case_id。

    Args:
        store: 知识库存储
        model_adapter: 提供 embed() 的适配器
        file_path: 案例文件路径（Markdown / 纯文本）

    Returns:
        case_id

    Raises:
        GalaxyDiagError: 文件读取失败
        ModelCallError: embedding 失败（向上传播，由调用方降级）
    """
    path = Path(file_path)
    if not path.exists():
        raise GalaxyDiagError(
            f"案例文件不存在: {file_path}",
            hint="请确认文件路径正确",
        )
    case = _build_case(file_path)

    # 增量判定：相同内容已存在则跳过
    existing = store.get(case.case_id)
    if existing is not None and existing.content_digest == case.content_digest:
        return case.case_id

    # embedding（整条案例为一个 chunk）
    vector = model_adapter.embed([case.content])[0]
    store.add(case, vector)
    return case.case_id


def reindex_all(store: KnowledgeStore, model_adapter) -> int:
    """全量重新计算所有案例的向量（embedding 模型更换后使用）

    Returns:
        重算的案例数

    Raises:
        ModelCallError: embedding 失败
    """
    cases = store.list_cases()
    if not cases:
        return 0
    contents = [c.content for c in cases]
    vectors = model_adapter.embed(contents)
    for case, vec in zip(cases, vectors):
        store.add(case, vec)
    return len(cases)
