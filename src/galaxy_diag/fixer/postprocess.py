"""LLM 输出后处理（REQ-D-01 / D-02 / D-03 不确定性声明）

处理流水线：JSON 提取 → Schema 校验 → 语义校验 → 构建 FixSuggestion。
校验失败时修复可修复项（补默认值 / 降级），source 标注为 LLM_FALLBACK。
JSON 完全解析失败时抛 FixerError，由 agent.py 重试。

对应设计文档 §LLM 输出后处理。
"""

from __future__ import annotations

import json
import re

from galaxy_diag.shared.errors import FixerError
from galaxy_diag.shared.types import (
    FixSource,
    FixStep,
    FixSuggestion,
)

# 占位符正则（与 template.py 保持一致）
_PLACEHOLDER_PATTERN = re.compile(r'<([A-Z_][A-Z0-9_]*)>')

# 重试时追加的 JSON 格式提示
_JSON_RETRY_SUFFIX = (
    "\n\n请重新输出，确保返回合法 JSON，格式如下：\n"
    '```json\n{"steps": [{"command": "...", "description": "...", '
    '"risk_note": "...", "parameters": {}, "is_verification": false}], '
    '"script_language": "bash", "risk_notes": [], "impact_scope": "..."}\n```'
)

# 验证步骤的只读命令前缀
_READ_ONLY_PREFIXES = (
    "ls", "cat", "df", "stat", "systemctl status",
    "kubectl get", "kubectl describe", "ping",
    "ip ", "ss ", "mount |", "free", "top",
)


# ===== JSON 提取（复用 diagnoser 的三策略） =====


def _extract_json(text: str) -> dict | None:
    """从 LLM 输出中提取 JSON

    三策略：直接解析 → markdown code block → 首个 {...} 块
    """
    # 策略 1：直接解析
    text = text.strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    # 策略 2：从 markdown code block 中提取
    code_block_match = re.search(r'```(?:json)?\s*\n(.*?)\n```', text, re.DOTALL)
    if code_block_match:
        try:
            data = json.loads(code_block_match.group(1).strip())
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    # 策略 3：提取首个 {...} 块
    brace_match = re.search(r'\{.*\}', text, re.DOTALL)
    if brace_match:
        try:
            data = json.loads(brace_match.group())
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    return None


# ===== Schema 校验 =====


def _validate_schema(data: dict) -> tuple[dict, bool]:
    """Schema 校验：检查必须字段存在、类型合法

    Returns:
        (校验后 dict, 是否做了修复)
    """
    repaired = False

    # steps 必须存在且为列表
    if "steps" not in data or not isinstance(data["steps"], list):
        data["steps"] = []
        repaired = True

    # 每个 step 的字段补全
    for step in data["steps"]:
        if not isinstance(step, dict):
            continue
        for key in ("command", "description", "risk_note"):
            if key not in step or not isinstance(step[key], str):
                step[key] = "" if key != "risk_note" else "未知风险"
                repaired = True
        if "parameters" not in step or not isinstance(step["parameters"], dict):
            step["parameters"] = {}
            repaired = True
        if "is_verification" not in step:
            step["is_verification"] = False
            repaired = True

    # script_language 可选，默认 bash
    if "script_language" not in data:
        data["script_language"] = "bash"
        repaired = True
    if data.get("script_language") not in ("bash", "python"):
        data["script_language"] = "bash"
        repaired = True

    # risk_notes / impact_scope 可选
    if "risk_notes" not in data or not isinstance(data.get("risk_notes"), list):
        data["risk_notes"] = []
        repaired = True
    if "impact_scope" not in data or not isinstance(data.get("impact_scope"), str):
        data["impact_scope"] = ""
        repaired = True

    return data, repaired


# ===== 语义校验 =====


def _validate_semantic(data: dict) -> tuple[dict, bool]:
    """语义校验：检查修复建议的合理性

    Returns:
        (校验后 dict, 是否做了修复)
    """
    repaired = False
    steps = data.get("steps", [])

    # 1. 空步骤：无法修复，返回标记
    if not steps:
        return data, True

    for step in steps:
        if not isinstance(step, dict):
            continue

        # 2. command 非空
        if not step.get("command"):
            step["command"] = "# TODO: 请手动填写命令"
            repaired = True

        # 3. description 非空
        if not step.get("description"):
            step["description"] = f"执行: {step.get('command', '未知')[:50]}"
            repaired = True

        # 4. risk_note 不能为空（D-01 验收标准：每条建议附带安全风险提示）
        if not step.get("risk_note"):
            step["risk_note"] = "请评估此操作的风险"
            repaired = True

        # 5. 验证步骤应为只读操作
        if step.get("is_verification"):
            cmd = step.get("command", "").strip()
            is_read_only = any(cmd.startswith(p) for p in _READ_ONLY_PREFIXES)
            if not is_read_only:
                step["is_verification"] = False  # 降级为非验证步骤
                repaired = True

        # 6. 占位符格式：识别 <UPPER_CASE> 模式，补到 parameters
        placeholders = _PLACEHOLDER_PATTERN.findall(step.get("command", ""))
        declared_params = set(step.get("parameters", {}).keys())
        for ph in placeholders:
            if ph not in declared_params:
                step.setdefault("parameters", {})[ph] = f"<{ph}>"
                repaired = True

    # 7. impact_scope 非空
    if not data.get("impact_scope") and steps:
        data["impact_scope"] = f"执行 {len(steps)} 个操作步骤"
        repaired = True

    return data, repaired


# ===== 构建 FixSuggestion =====


def _build_suggestion(data: dict, source: FixSource) -> FixSuggestion:
    """从校验后的 dict 构建 FixSuggestion"""
    steps = []
    for step_data in data.get("steps", []):
        if not isinstance(step_data, dict):
            continue
        steps.append(FixStep(
            command=step_data.get("command", ""),
            description=step_data.get("description", ""),
            risk_note=step_data.get("risk_note", "请评估此操作的风险"),
            parameters=step_data.get("parameters", {}),
            is_verification=step_data.get("is_verification", False),
        ))

    return FixSuggestion(
        steps=steps,
        script_language=data.get("script_language", "bash"),
        risk_notes=data.get("risk_notes", []),
        impact_scope=data.get("impact_scope", ""),
        source=source,
    )


# ===== 顶层接口 =====


def parse_fix_response(raw_response: str) -> FixSuggestion:
    """解析 LLM 原始输出为 FixSuggestion

    处理流水线：JSON 提取 → Schema 校验 → 语义校验 → 构建。
    JSON 完全解析失败时抛 FixerError（由 agent.py 重试）。
    """
    data = _extract_json(raw_response)
    if data is None:
        raise FixerError(
            "修复建议 JSON 解析失败",
            hint="LLM 未返回合法 JSON，将重试一次",
        )

    data, schema_repaired = _validate_schema(data)
    data, semantic_repaired = _validate_semantic(data)

    # 空步骤：无法修复
    if not data.get("steps"):
        raise FixerError(
            "LLM 未生成修复步骤",
            hint="诊断结论可能不明确，无法生成修复建议",
        )

    source = FixSource.LLM_FALLBACK if (schema_repaired or semantic_repaired) else FixSource.LLM
    return _build_suggestion(data, source)


def build_error_fallback(error_message: str) -> FixSuggestion:
    """构建 LLM 调用失败的降级兜底结果

    返回空步骤列表的 FixSuggestion（source=ERROR_FALLBACK），
    由 agent.py 判断是否继续进入 SECURITY_CHECKING。
    """
    return FixSuggestion(
        steps=[],
        script_language=None,
        risk_notes=[f"修复建议生成失败: {error_message}"],
        impact_scope="无法生成修复建议",
        source=FixSource.ERROR_FALLBACK,
    )
