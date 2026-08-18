"""LLM 输出后处理（REQ-C-03 不确定性声明）

处理流水线：JSON 提取 → Schema 校验 → 语义校验 → 构建 DiagnosisResult。
校验失败时修复可修复项（补默认值 / 降级 confidence），source 标注为 LLM_FALLBACK。
JSON 完全解析失败时抛 DiagnoseError，由 agent.py 重试。

对应设计文档 §LLM 输出后处理。
"""

from __future__ import annotations

import json
import re

from galaxy_diag.shared.errors import DiagnoseError
from galaxy_diag.shared.types import (
    Confidence,
    DiagnosisResult,
    DiagnosisSource,
    EnvironmentType,
)


# ===== JSON 提取 =====


def _extract_json(text: str) -> dict | None:
    """从 LLM 输出中提取 JSON 对象

    策略（按优先级尝试）：
    1. 直接解析整个文本
    2. 从 markdown code block 中提取
    3. 基于大括号配对的深度嵌套提取（处理 LLM 在 JSON 前后输出思考文本的情况）
    """
    # 1. 直接解析
    text = text.strip()
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # 2. Markdown code block
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        try:
            result = json.loads(m.group(1).strip())
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

    # 3. 基于大括号配对的深度嵌套提取
    #    小模型常在 JSON 前后输出思考过程，导致 JSON 被大量文本包裹。
    #    找到第一个 {，然后按配对数找到匹配的 }，提取中间部分。
    first_brace = text.find("{")
    if first_brace != -1:
        depth = 0
        for i in range(first_brace, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[first_brace : i + 1]
                    try:
                        result = json.loads(candidate)
                        if isinstance(result, dict):
                            return result
                    except json.JSONDecodeError:
                        # 这对 {} 不是有效 JSON，继续往后找下一个 {
                        pass
                    break

    return None


# ===== Schema 校验 =====

_VALID_CONFIDENCES = {"confirmed", "suspected", "insufficient"}


def _validate_schema(data: dict) -> tuple[dict, bool]:
    """Schema 校验：检查必须字段、类型、合法性

    Returns:
        (校验后 dict, 是否做了修复)
    """
    repaired = False

    # 确保必须字段存在
    for key in ("root_cause", "confidence", "evidence", "missing_info"):
        if key not in data:
            data[key] = "" if key in ("root_cause",) else []
            repaired = True

    # investigation_steps / fault_scope 可选
    if "investigation_steps" not in data:
        data["investigation_steps"] = []
        repaired = True
    if "fault_scope" not in data:
        data["fault_scope"] = ""
        repaired = True

    # confidence 合法性
    if not isinstance(data.get("confidence"), str) or data["confidence"] not in _VALID_CONFIDENCES:
        data["confidence"] = "suspected"  # 保守降级
        repaired = True

    return data, repaired


# ===== 语义校验 =====


def _validate_semantic(data: dict) -> tuple[dict, bool]:
    """语义校验：检查不确定性声明规则

    对应设计文档 §校验规则明细。
    修复可修复项，而非拒绝。

    Returns:
        (校验后 dict, 是否做了修复)
    """
    repaired = False
    conf = data.get("confidence", "insufficient")

    # CONFIRMED / SUSPECTED: evidence 非空
    if conf in ("confirmed", "suspected"):
        if not data.get("evidence"):
            if conf == "confirmed":
                data["confidence"] = "suspected"
            data["evidence"] = ["LLM 未提供证据"]
            repaired = True

        # root_cause 非空
        if not data.get("root_cause"):
            data["confidence"] = "insufficient"
            data["missing_info"] = data.get("missing_info") or ["LLM 推理未能给出根因"]
            data["investigation_steps"] = data.get("investigation_steps") or ["建议人工排查"]
            repaired = True

    # INSUFFICIENT: missing_info + investigation_steps 非空
    if conf == "insufficient":
        if not data.get("missing_info"):
            data["missing_info"] = ["未明确指出缺失信息"]
            repaired = True
        if not data.get("investigation_steps"):
            data["investigation_steps"] = ["建议人工排查"]
            repaired = True

    return data, repaired


# ===== 构建 DiagnosisResult =====


def _build_result(
    data: dict,
    env_type: EnvironmentType,
    source: DiagnosisSource,
) -> DiagnosisResult:
    """从校验后的 dict 构建 DiagnosisResult"""
    conf_str = data.get("confidence", "insufficient")
    try:
        confidence = Confidence(conf_str)
    except ValueError:
        confidence = Confidence.INSUFFICIENT

    return DiagnosisResult(
        root_cause=data.get("root_cause", ""),
        confidence=confidence,
        missing_info=data.get("missing_info", []),
        evidence=data.get("evidence", []),
        env_type=env_type,
        investigation_steps=data.get("investigation_steps", []),
        fault_scope=data.get("fault_scope", ""),
        diagnosis_source=source,
    )


# ===== 顶层接口 =====


def parse_diagnosis_response(
    raw_response: str,
    env_type: EnvironmentType,
) -> DiagnosisResult:
    """解析 LLM 原始输出为 DiagnosisResult

    处理流水线：JSON 提取 → Schema 校验 → 语义校验 → 构建。
    JSON 完全解析失败时抛 DiagnoseError（由 agent.py 重试）。
    Schema / 语义修复不抛异常，source 标注为 LLM_FALLBACK。

    Args:
        raw_response: LLM 返回的原始文本
        env_type: 当前环境类型

    Returns:
        DiagnosisResult（source=LLM 或 LLM_FALLBACK）

    Raises:
        DiagnoseError: JSON 完全解析失败
    """
    # 1. JSON 提取
    data = _extract_json(raw_response)
    if data is None:
        raise DiagnoseError(
            "LLM 输出 JSON 解析失败",
            hint="LLM 未返回合法 JSON，将重试一次",
        )

    # 2. Schema 校验
    data, schema_repaired = _validate_schema(data)

    # 3. 语义校验
    data, semantic_repaired = _validate_semantic(data)

    # 4. 确定来源
    source = DiagnosisSource.LLM_FALLBACK if (schema_repaired or semantic_repaired) else DiagnosisSource.LLM

    # 5. 构建
    return _build_result(data, env_type, source)


def build_error_fallback(env_type: EnvironmentType, error_message: str) -> DiagnosisResult:
    """构建 LLM 调用失败的降级兜底结果

    用于 agent.py 异常处理器：LLM 超时 / 连接失败等场景（服务不可用）。
    source=ERROR_FALLBACK，confidence=INSUFFICIENT。
    """
    return DiagnosisResult(
        root_cause="",
        confidence=Confidence.INSUFFICIENT,
        missing_info=[error_message],
        evidence=[],
        env_type=env_type,
        investigation_steps=[
            "建议检查 Ollama 服务状态: systemctl status ollama",
            "查看 Ollama 日志: journalctl -u ollama -n 50",
        ],
        fault_scope="推理服务不可用，无法完成根因分析",
        diagnosis_source=DiagnosisSource.ERROR_FALLBACK,
    )


def build_format_fallback(env_type: EnvironmentType, error_message: str) -> DiagnosisResult:
    """构建 LLM 输出格式异常的降级兜底结果

    用于 agent.py：LLM 能响应但输出格式异常（如 JSON 解析失败），非服务故障。
    区别于 ERROR_FALLBACK：模型实际可用，只是没遵循格式要求。
    source=FORMAT_FALLBACK，confidence=INSUFFICIENT。
    """
    return DiagnosisResult(
        root_cause="",
        confidence=Confidence.INSUFFICIENT,
        missing_info=[error_message],
        evidence=[],
        env_type=env_type,
        investigation_steps=[
            "模型输出格式异常，建议更换更大参数的模型或检查 Prompt",
        ],
        fault_scope="LLM 输出格式异常，未能生成结构化诊断结论",
        diagnosis_source=DiagnosisSource.FORMAT_FALLBACK,
    )
