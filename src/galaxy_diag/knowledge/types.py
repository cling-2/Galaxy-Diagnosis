"""客户知识库数据结构（REQ-X-02）

定义案例、检索引用、检索结果的数据类。
对应设计文档 §数据结构设计。

KnowledgeRef 定义在 shared/types.py（避免 shared → knowledge 反向依赖），
此处从 shared 导入复用。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from galaxy_diag.shared.types import EnvironmentType, KnowledgeRef


@dataclass
class KnowledgeCase:
    """一条客户案例（内存表示）"""

    case_id: str                                # 唯一标识（导入时生成）
    content: str                                # 案例全文
    content_digest: str                         # 内容摘要（hash），增量更新判定
    env_type: EnvironmentType | None = None     # frontmatter 解析；None=全环境适用
    tags: list[str] = field(default_factory=list)  # frontmatter 自由标签


@dataclass
class RetrievalResult:
    """检索结果（检索接口返回值）"""

    matches: list[tuple[KnowledgeCase, float]]  # (案例, 相似度)，按相似度降序
    query: str                                   # 实际查询文本（供 trace）
