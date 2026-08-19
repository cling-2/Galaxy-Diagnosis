"""知识库存储层（REQ-X-02）

加载/持久化 index.json + vectors.npy，案例 CRUD。
存储布局（对齐 persist.py 的 ~/.galaxy-diag 约定）：
  ~/.galaxy-diag/knowledge_base/
    cases/<case_id>.md     原始导入文件
    index.json             {case_id: {metadata, content_digest, vector_offset}}
    vectors.npy            向量矩阵（N × dim，行号 = vector_offset）
可通过 GALAXY_KB_DIR 环境变量覆盖目录。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

import numpy as np

from galaxy_diag.knowledge.types import KnowledgeCase
from galaxy_diag.shared.types import EnvironmentType

_DEFAULT_KB_DIR = Path.home() / ".galaxy-diag" / "knowledge_base"


def kb_dir() -> Path:
    """获取知识库存储目录（可通过 GALAXY_KB_DIR 覆盖）"""
    override = os.environ.get("GALAXY_KB_DIR")
    if override:
        return Path(override)
    return _DEFAULT_KB_DIR


class KnowledgeStore:
    """知识库存储：内存中维护案例列表 + 向量矩阵，落盘为 index.json + vectors.npy

    案例与向量通过 vector_offset 关联（矩阵行号）。
    """

    def __init__(self, base: Path):
        self._base = base
        self._cases: dict[str, KnowledgeCase] = {}     # case_id -> case
        self._offsets: dict[str, int] = {}             # case_id -> vector_offset
        self._vectors: list[list[float]] = []          # 行列表，顺序即 offset

    @property
    def cases(self) -> list[KnowledgeCase]:
        return list(self._cases.values())

    @property
    def vector_dim(self) -> int:
        return len(self._vectors[0]) if self._vectors else 0

    @classmethod
    def load(cls) -> "KnowledgeStore":
        """从磁盘加载知识库；目录不存在或为空时返回空 store"""
        base = kb_dir()
        store = cls(base)
        index_path = base / "index.json"
        vectors_path = base / "vectors.npy"
        if not index_path.exists():
            return store
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return store  # 损坏则视为空库
        if vectors_path.exists():
            try:
                # allow_pickle：维度不一致（模型更换）时 vectors.npy 为 object 数组
                store._vectors = np.load(vectors_path, allow_pickle=True).tolist()
            except (ValueError, OSError):
                store._vectors = []
        for case_id, meta in index.items():
            env = meta.get("env_type")
            case = KnowledgeCase(
                case_id=case_id,
                content=meta.get("content", ""),
                content_digest=meta.get("content_digest", ""),
                env_type=EnvironmentType(env) if env else None,
                tags=meta.get("tags", []),
            )
            store._cases[case_id] = case
            store._offsets[case_id] = meta.get("vector_offset", 0)
        return store

    def save(self) -> None:
        """持久化 index.json + vectors.npy + cases/ 原始文件"""
        self._base.mkdir(parents=True, exist_ok=True)
        (self._base / "cases").mkdir(exist_ok=True)
        # 向量矩阵（维度一致时为 N×dim 浮点矩阵；不一致时退化为 object 数组以保留数据，
        # 供 is_dimension_consistent() 检测模型更换场景）
        if self._vectors:
            try:
                arr = np.array(self._vectors, dtype=float)
            except (ValueError, TypeError):
                arr = np.array(self._vectors, dtype=object)
            np.save(self._base / "vectors.npy", arr)
        elif (self._base / "vectors.npy").exists():
            (self._base / "vectors.npy").unlink()
        # 索引
        index = {}
        for case_id, case in self._cases.items():
            index[case_id] = {
                "content_digest": case.content_digest,
                "env_type": case.env_type.value if case.env_type else None,
                "tags": case.tags,
                "content": case.content,
                "vector_offset": self._offsets.get(case_id, 0),
            }
        (self._base / "index.json").write_text(
            json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # 原始文件
        for case_id, case in self._cases.items():
            (self._base / "cases" / f"{case_id}.md").write_text(
                case.content, encoding="utf-8"
            )

    def add(self, case: KnowledgeCase, vector: list[float]) -> None:
        """新增/更新案例（同 case_id 则替换并更新向量）"""
        if case.case_id in self._offsets:
            offset = self._offsets[case.case_id]
            self._vectors[offset] = vector
        else:
            offset = len(self._vectors)
            self._vectors.append(vector)
            self._offsets[case.case_id] = offset
        self._cases[case.case_id] = case

    def get(self, case_id: str) -> KnowledgeCase | None:
        return self._cases.get(case_id)

    def delete(self, case_id: str) -> bool:
        """删除案例并重排向量矩阵"""
        if case_id not in self._cases:
            return False
        offset = self._offsets.pop(case_id)
        self._cases.pop(case_id)
        # 重排：删除该行，后续 offset 减 1
        self._vectors.pop(offset)
        for cid, off in list(self._offsets.items()):
            if off > offset:
                self._offsets[cid] = off - 1
        # 删除原始文件
        case_file = self._base / "cases" / f"{case_id}.md"
        if case_file.exists():
            case_file.unlink()
        return True

    def list_cases(self) -> list[KnowledgeCase]:
        return list(self._cases.values())

    def vector_of(self, case_id: str) -> list[float]:
        offset = self._offsets[case_id]
        return self._vectors[offset]

    def is_dimension_consistent(self) -> bool:
        """检测所有向量维度是否一致（embedding 模型更换后会不一致）"""
        if not self._vectors:
            return True
        dim = len(self._vectors[0])
        return all(len(v) == dim for v in self._vectors)
