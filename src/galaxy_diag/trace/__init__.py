"""Agent Trace 可观测性 (REQ-X-04)

提供诊断推理链路的记录与查询能力。

模块组成：
- recorder.py：TraceRecorder，推理链路记录（每步操作追加 trace 日志）
- viewer.py：trace 回放与查询（CLI 渲染）

对齐 docs/Trace_design.md。
"""

from galaxy_diag.trace.recorder import TraceRecorder, get_recorder

__all__ = ["TraceRecorder", "get_recorder"]
