"""诊断分析顶层入口（REQ-C-02 / C-03）

diagnose() 是 DIAGNOSING 步骤的唯一入口，供 engine.py _do_diagnosing 调用。
编排流程：规则匹配 → （可选）RAG 检索 → LLM 推理 → 后处理校验。
异常处理：LLM 调用失败明确提示降级（不静默吞没），JSON 解析失败重试 1 次。

对应设计文档 §顶层入口设计。
"""

from __future__ import annotations

from galaxy_diag.config.defaults import KnowledgeConfig
from galaxy_diag.diagnoser.postprocess import (
    build_error_fallback,
    build_format_fallback,
    parse_diagnosis_response,
)
from galaxy_diag.diagnoser.prompts import build_diagnosis_messages
from galaxy_diag.diagnoser.rules import match_rules
from galaxy_diag.knowledge.store import KnowledgeStore
from galaxy_diag.knowledge.types import RetrievalResult
from galaxy_diag.model.client import ModelAdapter
from galaxy_diag.shared.errors import DiagnoseError, ModelCallError
from galaxy_diag.shared.types import (
    DiagnosisResult,
    DiagnosisSource,
    DiagnosticContext,
    EnvInfo,
    KnowledgeRef,
)

# retrieve_similar 采用延迟导入（见 diagnose() 内），避免
# knowledge.retriever → diagnoser.rules → diagnoser.__init__ → agent → knowledge.retriever
# 的循环导入（当 knowledge 包先于 diagnoser 被导入时触发）。

# JSON 解析失败时追加的重试提示
_JSON_RETRY_SUFFIX = "\n\n[重要提示] 上次输出不是合法 JSON，请严格按指定 JSON 格式输出，不要包含其他文字。"


def diagnose(
    problem_description: str,
    env_info: EnvInfo,
    diagnostic_context: DiagnosticContext,
    model_adapter: ModelAdapter,
    kb_store: KnowledgeStore | None = None,
    knowledge_config: KnowledgeConfig | None = None,
) -> DiagnosisResult:
    """DIAGNOSING 顶层入口：规则匹配 → LLM 推理 → 后处理

    Args:
        problem_description: 用户问题描述
        env_info: 环境感知产出（B-01）
        diagnostic_context: 诊断信息采集产出（C-01）
        model_adapter: LLM 调用入口（model/client.py）
        kb_store: 客户知识库存储（可选，非空时启用 RAG 检索增强）
        knowledge_config: 知识库检索配置（可选，与 kb_store 同时非空时启用 RAG）

    Returns:
        DiagnosisResult: 带置信度标签的诊断结论（source 标注来源）

    Notes:
        kb_store/knowledge_config 非空且 model_adapter.config.embed_model 已配置时
        启用 RAG 检索增强；规则命中时不触发 RAG（规则快路径直接返回）。
        embed_model 未配置时不启用 RAG，避免无意义的 embed() 调用。
    """
    # 1. 规则匹配快路径（DIAGNOSING 内）
    #    注：COLLECTING 末尾已对 CONFIRMED 短路；此处主要处理 SUSPECTED 命中
    rule_result = match_rules(diagnostic_context)
    if rule_result is not None:
        rule_result.diagnosis_source = DiagnosisSource.RULE_MATCH
        return rule_result

    # 2. LLM 推理深路径
    env_type = env_info.env_type

    # 2a. RAG 检索增强（仅规则未命中时；规则命中已在上方 return）
    #     embed_model 未配置时不启用 RAG，避免无意义的 embed() 调用
    rag_enabled = (
        kb_store is not None
        and knowledge_config is not None
        and bool(getattr(model_adapter.config, "embed_model", ""))
    )
    retrieval_result: RetrievalResult | None = None
    if rag_enabled:
        # 延迟导入：打破 knowledge.retriever ↔ diagnoser.agent 循环依赖
        from galaxy_diag.knowledge.retriever import retrieve_similar

        retrieval_result = retrieve_similar(
            diagnostic_context, env_info, model_adapter, kb_store, knowledge_config
        )

    try:
        messages = build_diagnosis_messages(
            problem_description, env_info, diagnostic_context, retrieval_result
        )
        raw_response = model_adapter.chat(messages)
        result = parse_diagnosis_response(raw_response, env_type)

        # 来源标注：填充引用的客户案例
        if retrieval_result and retrieval_result.matches:
            result.referenced_knowledge = [
                KnowledgeRef(
                    case_id=case.case_id,
                    similarity=sim,
                    summary=case.content[:60],
                )
                for case, sim in retrieval_result.matches
            ]
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

        # 来源标注：填充引用的客户案例（重试路径同样填充）
        if retrieval_result and retrieval_result.matches:
            result.referenced_knowledge = [
                KnowledgeRef(
                    case_id=case.case_id, similarity=sim, summary=case.content[:60],
                )
                for case, sim in retrieval_result.matches
            ]
        return result
    except ModelCallError:
        # 重试时服务故障：明确提示，降级兜底
        return build_error_fallback(env_type, "LLM 推理服务不可用，无法完成根因分析")
    except DiagnoseError:
        # 重试仍格式异常：模型可用但未遵循格式要求
        return build_format_fallback(env_type, "LLM 推理输出格式异常，无法解析")
