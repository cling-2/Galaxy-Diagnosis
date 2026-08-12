"""修复生成顶层入口（REQ-D-01 / D-02 / D-03）

generate() 是 PLANNING 步骤的唯一入口，供 engine.py _do_planning 调用。
编排流程：Prompt 组装 → LLM 推理 → 后处理校验 → 模板渲染 → 脚本组装。
异常处理：LLM 调用失败明确提示降级（不静默吞没），JSON 解析失败重试 1 次。

对应设计文档 §顶层入口设计。
"""

from __future__ import annotations

from galaxy_diag.fixer.generator import generate_script
from galaxy_diag.fixer.postprocess import (
    _JSON_RETRY_SUFFIX,
    build_error_fallback,
    parse_fix_response,
)
from galaxy_diag.fixer.prompts import build_fix_messages
from galaxy_diag.fixer.template import render_all
from galaxy_diag.model.client import ModelAdapter
from galaxy_diag.shared.errors import FixerError, ModelCallError
from galaxy_diag.shared.types import (
    DiagnosisResult,
    EnvInfo,
    FixProposal,
    FixSource,
    FixSuggestion,
)


def generate(
    diagnosis: DiagnosisResult,
    env_info: EnvInfo,
    model_adapter: ModelAdapter,
) -> FixProposal:
    """PLANNING 顶层入口：LLM 生成 → 后处理 → 模板渲染 → 脚本组装

    Args:
        diagnosis: 诊断结论（来自 DIAGNOSING）
        env_info: 环境感知产出（来自 ENV_RECOGNISING）
        model_adapter: LLM 调用入口

    Returns:
        FixProposal: 修复建议（尚未经过多维检测，检测在 SECURITY_CHECKING 步骤执行）
    """
    # 1. 组装 LLM 消息
    messages = build_fix_messages(diagnosis, env_info)

    # 2. LLM 生成修复建议（含重试逻辑）
    suggestion: FixSuggestion | None = None
    raw_response: str = ""

    try:
        raw_response = model_adapter.chat(messages)
        suggestion = parse_fix_response(raw_response)
    except FixerError:
        # JSON 解析失败：重试 1 次（追加 JSON 格式提示）
        pass
    except ModelCallError:
        # LLM 调用失败：降级兜底
        suggestion = build_error_fallback("LLM 推理服务不可用，无法生成修复建议")

    # 重试逻辑
    if suggestion is None:
        try:
            retry_messages = messages + [
                {"role": "assistant", "content": raw_response},
                {"role": "user", "content": _JSON_RETRY_SUFFIX},
            ]
            raw_response_retry = model_adapter.chat(retry_messages)
            suggestion = parse_fix_response(raw_response_retry)
        except (FixerError, ModelCallError):
            suggestion = build_error_fallback("LLM 输出格式异常，无法生成修复建议")

    # 3. ERROR_FALLBACK：空步骤列表
    if not suggestion.steps:
        return FixProposal(
            risk_notes=suggestion.risk_notes,
            impact_scope=suggestion.impact_scope,
            source=FixSource.ERROR_FALLBACK,
        )

    # 4. 模板渲染：FixStep → CommandTemplate
    commands = render_all(suggestion.steps)

    # 5. 脚本组装（仅多步修复时）
    non_verify_cmds = [c for c in commands if not c.is_verification]
    script: str | None = None
    script_language = suggestion.script_language if len(non_verify_cmds) >= 2 else None
    if len(non_verify_cmds) >= 2:
        script = generate_script(
            commands=non_verify_cmds,
            language=suggestion.script_language or "bash",
            root_cause=diagnosis.root_cause,
        )

    # 6. 组装 FixProposal（检测在 SECURITY_CHECKING 步骤执行）
    return FixProposal(
        commands=commands,
        script=script,
        script_language=script_language,
        risk_notes=suggestion.risk_notes,
        impact_scope=suggestion.impact_scope,
        source=suggestion.source,
    )
