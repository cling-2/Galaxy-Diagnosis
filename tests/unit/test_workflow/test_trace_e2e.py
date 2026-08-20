"""Trace 端到端集成测试 (REQ-X-04)

验证完整工作流运行后生成 trace 文件，且 trace 内容满足验收标准。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from galaxy_diag.shared.types import (
    Confidence,
    DiagnosisResult,
    DiagnosisSource,
    EnvInfo,
    EnvironmentType,
    HardwareInfo,
    WorkflowState,
    WorkflowStep,
)
from galaxy_diag.trace import viewer


def _make_state() -> WorkflowState:
    """构造带 prematch CONFIRMED 的最小 WorkflowState（跳过 COLLECTING/DIAGNOSING）"""
    return WorkflowState(
        session_id="trace-e2e-test",
        current_step=WorkflowStep.ENV_RECOGNISING,
        problem_description="galaxy-api 服务异常退出",
        should_skip_collecting=True,
        env_info=EnvInfo(
            env_type=EnvironmentType.CONTAINER,
            hardware=HardwareInfo(
                cpu_model="Intel",
                cpu_cores=4,
                memory_total_gb=16.0,
                disks=[],
                raid_cards=[],
                nics=[],
            ),
            storage=[],
            has_docker_cli=True,
            has_kubectl_cli=False,
        ),
        diagnosis=DiagnosisResult(
            root_cause="galaxy-api 服务异常退出",
            confidence=Confidence.CONFIRMED,
            evidence=["已知故障模式匹配"],
            missing_info=[],
            env_type=EnvironmentType.CONTAINER,
            investigation_steps=[],
            fault_scope="服务层",
            diagnosis_source=DiagnosisSource.RULE_MATCH,
        ),
    )


class TestTraceEndToEnd:
    """Trace 端到端：工作流运行后 trace 文件存在且结构正确"""

    def test_trace_file_created_after_workflow_step(
        self, monkeypatch, tmp_path
    ) -> None:
        """_do_env_recognising 执行后 trace 文件存在"""
        session_dir = tmp_path / "sessions"
        session_dir.mkdir()
        trace_dir = tmp_path / "traces"
        trace_dir.mkdir()
        monkeypatch.setenv("GALAXY_SESSION_DIR", str(session_dir))

        from galaxy_diag.workflow.engine import WorkflowEngine

        state = _make_state()
        engine = WorkflowEngine(state, auto=True, mock=True)

        # 手动启动 trace（模拟 run() 中的 _start_trace）
        # 使用临时 trace_dir
        from galaxy_diag.trace.recorder import TraceRecorder, set_recorder

        recorder = TraceRecorder(
            session_id=state.session_id,
            problem_description=state.problem_description,
            trace_dir=trace_dir,
        )
        engine._recorder = recorder
        engine._recorder_token = set_recorder(recorder)

        # 执行一步
        engine._do_env_recognising()

        # 关闭 trace
        recorder.close_trace("done")
        from galaxy_diag.trace.recorder import reset_recorder

        reset_recorder(engine._recorder_token)

        # 验证 trace 文件存在
        trace_path = trace_dir / "trace-e2e-test.jsonl"
        assert trace_path.exists(), "trace JSONL 文件应存在"

        # 验证 trace 可加载
        tree = viewer.load_trace("trace-e2e-test", trace_dir=trace_dir)
        assert tree is not None, "trace 应可加载"
        assert tree.session_id == "trace-e2e-test"
        assert tree.problem_description == "galaxy-api 服务异常退出"

    def test_trace_contains_expected_event_types(
        self, monkeypatch, tmp_path
    ) -> None:
        """trace 包含预期的 Event 类型"""
        trace_dir = tmp_path / "traces"
        trace_dir.mkdir()

        from galaxy_diag.trace.recorder import TraceRecorder

        r = TraceRecorder("sess-events", "问题", trace_dir=trace_dir)

        with r.span("env_recognising", 1):
            r.record_event("RuleMatch", rules_count=5, result="NONE", matched_rule_id=None, matched_keywords=[], rule_hint=None, diagnosis_source="LLM", status="success")

        with r.span("diagnosing", 1):
            r.record_event("RuleMatch", rules_count=5, result="CONFIRMED", matched_rule_id="rule_1", matched_keywords=[], rule_hint=None, diagnosis_source="RULE_MATCH", status="success")
            r.record_event("LLMCall", model="qwen3:1.7b", completion="result", status="success", parse_ok=None)
            r.update_last_events("LLMCall", parse_ok=True, parsed_result={"root_cause": "x"})

        with r.span("reviewing", 1):
            r.record_event("HITL", type="review_confirm", decision="confirmed", guard_level="pass", edited_fields=None, impact="执行")
            r.record_event("SecurityCheck", check_type="execution_guard", guard_level="pass", matched_patterns=[], impact_summary=None, message=None, status="success")

        r.close_trace("done")

        tree = viewer.load_trace("sess-events", trace_dir=trace_dir)
        assert tree is not None

        event_types = [e.event_type for s in tree.spans for e in s.events]
        assert "RuleMatch" in event_types
        assert "LLMCall" in event_types
        assert "HITL" in event_types
        assert "SecurityCheck" in event_types

        # 验收标准 4：推理链路内容与诊断结论一致
        # LLMCall 的 parsed_result 应有 root_cause
        llm_events = [e for s in tree.spans for e in s.events if e.event_type == "LLMCall"]
        assert len(llm_events) == 1
        assert llm_events[0].data["parse_ok"] is True
        assert llm_events[0].data["parsed_result"]["root_cause"] == "x"

    def test_trace_persists_across_restart(self, tmp_path) -> None:
        """验收标准 3：推理链路持久化存储，服务重启后可查询"""
        trace_dir = tmp_path / "traces"
        trace_dir.mkdir()

        from galaxy_diag.trace.recorder import TraceRecorder

        # 第一次运行
        r1 = TraceRecorder("sess-persist", "问题", trace_dir=trace_dir)
        with r1.span("env_recognising", 1):
            r1.record_event("RuleMatch", rules_count=5, result="NONE", matched_rule_id=None, matched_keywords=[], rule_hint=None, diagnosis_source="LLM", status="success")
        r1.close_trace("interrupted")

        # 模拟重启：新建 recorder 追加到同一文件
        r2 = TraceRecorder("sess-persist", "问题", trace_dir=trace_dir)
        with r2.span("diagnosing", 1):
            r2.record_event("LLMCall", model="m", completion="c", status="success", parse_ok=True)
        r2.close_trace("done")

        # 查询：可加载完整 trace
        tree = viewer.load_trace("sess-persist", trace_dir=trace_dir)
        assert tree is not None
        assert tree.final_status == "done"
        # 两个 span（第一次的 env_recognising + 第二次的 diagnosing）
        assert len(tree.spans) == 2

    def test_trace_acceptance_criteria_1_complete_chain(
        self, tmp_path
    ) -> None:
        """验收标准 1：每次诊断记录完整的推理链路（调用了哪些工具、得到了什么结果、基于什么逻辑得出结论）"""
        trace_dir = tmp_path / "traces"
        trace_dir.mkdir()

        from galaxy_diag.trace.recorder import TraceRecorder

        r = TraceRecorder("sess-chain", "问题", trace_dir=trace_dir)

        with r.span("collecting", 1):
            r.record_event("ToolCall", tool_name="collect_system_resources", input_params={}, output_summary="CPU 4核 MEM 16G", output_size_bytes=100, output_status="success", status="success")
            r.record_event("ToolCall", tool_name="collect_component_status", input_params={}, output_summary="galaxy-api DOWN", output_size_bytes=50, output_status="success", status="success")

        with r.span("diagnosing", 1):
            r.record_event("RuleMatch", rules_count=5, result="CONFIRMED", matched_rule_id="rule_api_down", matched_keywords=["galaxy-api", "DOWN"], rule_hint=None, diagnosis_source="RULE_MATCH", status="success")

        r.close_trace("done")

        tree = viewer.load_trace("sess-chain", trace_dir=trace_dir)
        assert tree is not None

        # 完整链路：ToolCall → ToolCall → RuleMatch → 结论
        tool_events = [e for s in tree.spans for e in s.events if e.event_type == "ToolCall"]
        rule_events = [e for s in tree.spans for e in s.events if e.event_type == "RuleMatch"]
        assert len(tool_events) == 2, "应记录 2 次 Tool 调用"
        assert tool_events[0].data["tool_name"] == "collect_system_resources"
        assert tool_events[1].data["tool_name"] == "collect_component_status"
        assert len(rule_events) == 1
        assert rule_events[0].data["result"] == "CONFIRMED"

    def test_trace_acceptance_criteria_2_command_view(self, tmp_path) -> None:
        """验收标准 2：用户可通过命令查看指定诊断任务的推理过程"""
        trace_dir = tmp_path / "traces"
        trace_dir.mkdir()

        from galaxy_diag.trace.recorder import TraceRecorder

        r = TraceRecorder("sess-cmd", "问题", trace_dir=trace_dir)
        with r.span("diagnosing", 1):
            r.record_event("RuleMatch", rules_count=5, result="CONFIRMED", matched_rule_id="r1", matched_keywords=[], rule_hint=None, diagnosis_source="RULE_MATCH", status="success")
        r.close_trace("done")

        # 模拟 CLI 查询
        tree = viewer.load_trace("sess-cmd", trace_dir=trace_dir)
        assert tree is not None, "galaxy-diag trace <session_id> 应能查看"
        assert len(tree.spans) == 1
        assert len(tree.spans[0].events) == 1
