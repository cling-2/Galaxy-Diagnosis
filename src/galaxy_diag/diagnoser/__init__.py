"""诊断信息采集模块（REQ-C-01）

顶层编排：关键词→Tool 映射 → 定向采集 → 预处理 → 组装 DiagnosticContext。

对齐 Diagnostic_collection_design.md §整体架构设计。
"""

from galaxy_diag.diagnoser.context import build_diagnostic_context

__all__ = ["build_diagnostic_context"]
