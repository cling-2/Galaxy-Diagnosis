"""诊断分析模块（REQ-C-01 / C-02 / C-03）

C-01 信息采集：关键词→Tool 映射 → 定向采集 → 预处理 → 组装 DiagnosticContext
C-02 根因分析：规则匹配快路径 + LLM 推理深路径
C-03 不确定性声明：置信度标签 + 后处理校验

对齐 Diagnostic_Analysis_design.md。
"""

from galaxy_diag.diagnoser.agent import diagnose
from galaxy_diag.diagnoser.context import build_diagnostic_context

__all__ = ["build_diagnostic_context", "diagnose"]
