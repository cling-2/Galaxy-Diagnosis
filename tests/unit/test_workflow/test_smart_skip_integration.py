"""智能跳过与反幻觉集成测试"""

from unittest.mock import patch, MagicMock

import pytest

from galaxy_diag.shared.types import (
    Confidence,
    DiagnosisResult,
    DiagnosisSource,
    DiagnosticContext,
    EnvironmentType,
    EnvInfo,
    HardwareInfo,
    LogSnippet,
    WorkflowState,
    WorkflowStep,
)


class TestBTypeSkipCollecting:
    """B类：已知故障模式跳过 COLLECTING"""

    def test_crashloop_skips_collecting(self):
        """Pod CrashLoopBackOff → should_skip_collecting=True"""
        from galaxy_diag.diagnoser.rules import prematch_rules_by_description
        result = prematch_rules_by_description(
            "Pod CrashLoopBackOff", EnvironmentType.CONTAINER,
        )
        assert result is not None
        assert result.confidence == Confidence.CONFIRMED

    def test_io_error_skips_collecting(self):
        """I/O error → should_skip_collecting=True"""
        from galaxy_diag.diagnoser.rules import prematch_rules_by_description
        result = prematch_rules_by_description(
            "磁盘 I/O error", EnvironmentType.BARE_METAL,
        )
        assert result is not None
        assert result.confidence == Confidence.CONFIRMED

    def test_suspected_does_not_skip(self):
        """SUSPECTED 规则不触发跳过"""
        from galaxy_diag.diagnoser.rules import prematch_rules_by_description
        # "unreachable" alone → network_unreachable is SUSPECTED
        result = prematch_rules_by_description(
            "目标 unreachable", EnvironmentType.BARE_METAL,
        )
        assert result is None


class TestCTypeSkipHardware:
    """C类：按需精简硬件采集"""

    def test_network_problem_skips_hardware(self):
        from galaxy_diag.diagnoser.context import should_collect_hardware
        assert should_collect_hardware("网络不通 ping 不通") is False

    def test_disk_problem_collects_hardware(self):
        from galaxy_diag.diagnoser.context import should_collect_hardware
        assert should_collect_hardware("磁盘 I/O error") is True


class TestHallucinationGuard:
    """反幻觉事实校验"""

    def test_network_contradiction(self):
        """网络问题但全部可达 → 矛盾"""
        from galaxy_diag.diagnoser.hallucination_guard import check_facts
        ctx = DiagnosticContext(
            problem_description="网络不通",
            network_checks=[
                {"target": "10.0.1.1", "reachable": True, "detail": "ok"},
            ],
        )
        result = check_facts("网络不通", ctx)
        assert result is not None
        assert result.contradiction is True

    def test_no_contradiction(self):
        """I/O error 不触发反幻觉（无校验规则）"""
        from galaxy_diag.diagnoser.hallucination_guard import check_facts
        ctx = DiagnosticContext(
            problem_description="I/O error",
            log_snippets=[LogSnippet(source="dmesg", level="ERROR", content="I/O error")],
        )
        result = check_facts("I/O error", ctx)
        assert result is None


class TestWorkflowStateFlow:
    """工作流状态转换验证"""

    def test_env_recognising_to_planning_is_valid(self):
        """ENV_RECOGNISING → PLANNING 合法"""
        from galaxy_diag.workflow.states import is_valid_transition
        assert is_valid_transition(WorkflowStep.ENV_RECOGNISING, WorkflowStep.PLANNING)

    def test_env_recognising_to_collecting_still_valid(self):
        """ENV_RECOGNISING → COLLECTING 仍合法"""
        from galaxy_diag.workflow.states import is_valid_transition
        assert is_valid_transition(WorkflowStep.ENV_RECOGNISING, WorkflowStep.COLLECTING)

    def test_state_persistence_with_new_fields(self):
        """WorkflowState 新字段可序列化/反序列化"""
        from galaxy_diag.workflow.persist import save_state, load_state
        import tempfile
        import os

        state = WorkflowState(
            session_id="test_smart_skip",
            current_step=WorkflowStep.ENV_RECOGNISING,
            problem_description="Pod CrashLoopBackOff",
            should_skip_collecting=True,
            should_skip_hardware=False,
            hallucination_check_result="network_ok",
        )

        # 保存到临时目录
        with patch.dict(os.environ, {"GALAXY_SESSION_DIR": tempfile.mkdtemp()}):
            save_state(state)
            loaded = load_state("test_smart_skip")

        assert loaded.should_skip_collecting is True
        assert loaded.should_skip_hardware is False
        assert loaded.hallucination_check_result == "network_ok"
