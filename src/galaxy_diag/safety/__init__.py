"""安全可控模块（REQ-E-01 ~ E-04, REQ-F-03）

红线 2 物理实现层：LLM 只能"建议"，"决定权"在硬编码逻辑。
四条安全关键路径全部不经 LLM：
1. 危险操作拦截 — danger.py 正则 + 硬编码算法
2. 人工审核确认 — review.py stdin 专用通道
3. 操作快照与回滚 — snapshot.py 文件系统操作
4. 审计日志 — audit.py 专用函数写 JSONL

对齐 Safety_design.md。
"""

from galaxy_diag.safety.audit import build_record, query_audit, write_audit
from galaxy_diag.safety.danger import execution_guard_check
from galaxy_diag.safety.executor import run as execute
from galaxy_diag.safety.patterns import DANGER_PATTERNS
from galaxy_diag.safety.review import needs_confirm, review_confirm
from galaxy_diag.safety.snapshot import create_snapshot, list_snapshots, rollback
from galaxy_diag.safety.verifier import verify

__all__ = [
    # E-02 危险防护
    "execution_guard_check",
    "DANGER_PATTERNS",
    # E-01/F-03 人工审核
    "review_confirm",
    "needs_confirm",
    # E-03 快照回滚
    "create_snapshot",
    "rollback",
    "list_snapshots",
    # 执行
    "execute",
    # 验证
    "verify",
    # E-04 审计日志
    "write_audit",
    "query_audit",
    "build_record",
]
