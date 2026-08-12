"""修复生成模块（REQ-D-01 / D-02 / D-03）

D-01 修复命令建议模板：占位符参数化、可编辑、安全风险提示
D-02 修复脚本生成：多步骤编排、错误处理逻辑
D-03 生成代码多维错误检测：语法/危险/兼容性

对齐 Fix_Generation_design.md。
"""

from galaxy_diag.fixer.agent import generate

__all__ = ["generate"]
