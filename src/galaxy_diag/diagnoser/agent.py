"""诊断分析顶层入口（REQ-C-02 / C-03）

diagnose() 是 DIAGNOSING 步骤的唯一入口，供 engine.py _do_diagnosing 调用。
编排流程：规则匹配 → LLM 推理 → 后处理校验。
异常处理：LLM 调用失败明确提示降级（不静默吞没），JSON 解析失败重试 1 次。

对应设计文档 §顶层入口设计。
"""

from __future__ import annotations

from galaxy_diag.diagnoser.postprocess import build_error_fallback, parse_diagnosis_response
from galaxy_diag.diagnoser.prompts import build_diagnosis_messages
from galaxy_diag.diagnoser.rules import match_rules
from galaxy_diag.model.client import ModelAdapter
from galaxy_diag.shared.errors import DiagnoseError, ModelCallError
from galaxy_diag.shared.types import (
    DiagnosisResult,
    DiagnosisSource,
    DiagnosticContext,
    EnvInfo,
)

# JSON 解析失败时追加的重试提示
_JSON_RETRY_SUFFIX = "\n\n[重要提示] 上次输出不是合法 JSON，请严格按指定 JSON 格式输出，不要包含其他文字。"


def diagnose(
    problem_description: str,
    env_info: EnvInfo,
    diagnostic_context: DiagnosticContext,
    model_adapter: ModelAdapter,
) -> DiagnosisResult:
    """DIAGNOSING 顶层入口：规则匹配 → LLM 推理 → 后处理

    Args:
        problem_description: 用户问题描述
        env_info: 环境感知产出（B-01）
        diagnostic_context: 诊断信息采集产出（C-01）
        model_adapter: LLM 调用入口（model/client.py）

    Returns:
        DiagnosisResult: 带置信度标签的诊断结论（source 标注来源）
    """
    # 1. 规则匹配快路径（DIAGNOSING 内）
    #    注：COLLECTING 末尾已对 CONFIRMED 短路；此处主要处理 SUSPECTED 命中
    rule_result = match_rules(diagnostic_context)
    if rule_result is not None:
        rule_result.diagnosis_source = DiagnosisSource.RULE_MATCH
        return rule_result

    # 2. LLM 推理深路径
    env_type = env_info.env_type

    try:
        messages = build_diagnosis_messages(problem_description, env_info, diagnostic_context)
        raw_response = model_adapter.chat(messages)
        result = parse_diagnosis_response(raw_response, env_type)
        return result
    except DiagnoseError:
        # JSON 解析失败：重试 1 次
        pass
    except ModelCallError:
        # LLM 调用失败：明确提示，降级兜底
        return build_error_fallback(env_type, "LLM 推理服务不可用，无法完成根因分析")

    # 重试 1 次（追加 JSON 格式提示）
    try:
        retry_messages = messages + [
            {"role": "assistant", "content": raw_response},
            {"role": "user", "content": _JSON_RETRY_SUFFIX},
        ]
        raw_response_retry = model_adapter.chat(retry_messages)
        result = parse_diagnosis_response(raw_response_retry, env_type)
        return result
    except (DiagnoseError, ModelCallError):
        # 重试仍失败：降级兜底
        return build_error_fallback(env_type, "LLM 推理输出格式异常，无法解析")
