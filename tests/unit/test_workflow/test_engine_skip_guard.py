"""B 类跳过守卫测试：_do_collecting 跳过时应转至 PLANNING 而非 DIAGNOSING

回归场景：resume 边界情况，工作流停在 COLLECTING 且
should_skip_collecting=True，此时 _do_collecting 应转入
PLANNING（跳过 DIAGNOSING），而非 DIAGNOSING（会导致
diagnostic_context=None 引发 WorkflowError 崩溃）。
"""

from unittest.mock import MagicMock, patch

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
from galaxy_diag.workflow.engine import WorkflowEngine


def _make_state_with_skip() -> WorkflowState:
    """构造 resume 边界状态：current_step=COLLECTING, should_skip_collecting=True"""
    return WorkflowState(
        session_id="test_skip_guard",
        current_step=WorkflowStep.COLLECTING,
        problem_description="galaxy-api 服务异常",
        env_info=EnvInfo(
            env_type=EnvironmentType.CONTAINER,
            hardware=HardwareInfo(
                cpu_model="Intel",
                cpu_cores=4,
                memory_total_gb=8.0,
                disks=[],
                raid_cards=[],
                nics=[],
            ),
            storage=[],
            has_docker_cli=False,
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
        should_skip_collecting=True,
    )


def _make_engine(state: WorkflowState) -> WorkflowEngine:
    engine = WorkflowEngine(state, auto=True, mock=True)
    return engine


class TestCollectingSkipGuardTarget:
    """_do_collecting 跳过守卫目标应为 PLANNING，而非 DIAGNOSING"""

    def test_skip_goes_to_planning_not_diagnosing(self, monkeypatch, tmp_path):
        """resume 边界：COLLECTING + should_skip_collecting=True 应转入 PLANNING"""
        session_dir = tmp_path / "sessions"
        session_dir.mkdir()
        monkeypatch.setenv("GALAXY_SESSION_DIR", str(session_dir))

        state = _make_state_with_skip()
        engine = _make_engine(state)

        # _do_collecting 内部会调用 _transition → _save（写持久化），
        # 需要让 save_state 不报错即可（已有 GALAXY_SESSION_DIR）。
        engine._do_collecting()

        assert engine.state.current_step == WorkflowStep.PLANNING, (
            f"跳过守卫应转入 PLANNING，实际转入 {engine.state.current_step.value}"
        )

    def test_skip_does_not_enter_diagnosing(self, monkeypatch, tmp_path):
        """跳过守卫不应进入 DIAGNOSING（会导致 diagnostic_context=None 崩溃）"""
        session_dir = tmp_path / "sessions"
        session_dir.mkdir()
        monkeypatch.setenv("GALAXY_SESSION_DIR", str(session_dir))

        state = _make_state_with_skip()
        engine = _make_engine(state)

        engine._do_collecting()

        assert engine.state.current_step != WorkflowStep.DIAGNOSING, (
            "跳过守卫不应转入 DIAGNOSING，此时 diagnostic_context=None 会导致崩溃"
        )

    def test_collecting_to_planning_is_valid_transition(self):
        """COLLECTING → PLANNING 是合法转换（已存在于 TRANSITIONS 表）"""
        from galaxy_diag.workflow.states import is_valid_transition

        assert is_valid_transition(WorkflowStep.COLLECTING, WorkflowStep.PLANNING) is True

    def test_diagnosis_preserved_after_skip(self, monkeypatch, tmp_path):
        """跳过后 diagnosis 不丢失（PLANNING 需要它）"""
        session_dir = tmp_path / "sessions"
        session_dir.mkdir()
        monkeypatch.setenv("GALAXY_SESSION_DIR", str(session_dir))

        state = _make_state_with_skip()
        original_diagnosis = state.diagnosis
        engine = _make_engine(state)

        engine._do_collecting()

        assert engine.state.diagnosis is not None
        assert engine.state.diagnosis.root_cause == original_diagnosis.root_cause
