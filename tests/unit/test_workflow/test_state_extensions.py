"""WorkflowState 扩展字段 + 状态转换测试"""
from galaxy_diag.shared.types import WorkflowState, WorkflowStep
from galaxy_diag.workflow.persist import load_state, save_state
from galaxy_diag.workflow.states import (
    SKIP_TARGETS, is_valid_transition,
)

class TestWorkflowStateExtensions:
    def test_should_skip_collecting_default_false(self):
        state = WorkflowState()
        assert state.should_skip_collecting is False

    def test_should_skip_hardware_default_false(self):
        state = WorkflowState()
        assert state.should_skip_hardware is False

    def test_hallucination_check_result_default_none(self):
        state = WorkflowState()
        assert state.hallucination_check_result is None

class TestNewTransitions:
    def test_env_recognising_to_planning_is_valid(self):
        """ENV_RECOGNISING → PLANNING 是合法转换（B类跳过）"""
        assert is_valid_transition(WorkflowStep.ENV_RECOGNISING, WorkflowStep.PLANNING) is True

    def test_env_recognising_skip_target_includes_planning(self):
        """SKIP_TARGETS 包含 ENV_RECOGNISING → PLANNING"""
        assert WorkflowStep.PLANNING in SKIP_TARGETS.get(WorkflowStep.ENV_RECOGNISING, [])

    def test_env_recognising_to_collecting_still_valid(self):
        """原路径 ENV_RECOGNISING → COLLECTING 仍有效"""
        assert is_valid_transition(WorkflowStep.ENV_RECOGNISING, WorkflowStep.COLLECTING) is True


class TestNewFieldsPersistRoundtrip:
    """新字段持久化往返测试

    验证 save_state → load_state 后，B/C 类跳过标志与反幻觉校验结果
    能正确序列化→反序列化（防 persist 反序列化回归）。
    对齐 test_persist_roundtrip.py 的 GALAXY_SESSION_DIR + tempdir 模式。
    """

    def test_new_fields_roundtrip(self, monkeypatch, tmp_path):
        session_dir = tmp_path / "sessions"
        session_dir.mkdir()
        monkeypatch.setenv("GALAXY_SESSION_DIR", str(session_dir))

        session_id = "sess_new_fields_001"
        original = WorkflowState(
            session_id=session_id,
            current_step=WorkflowStep.PLANNING,
            should_skip_collecting=True,
            should_skip_hardware=False,
            hallucination_check_result="network_ok",
        )

        save_state(original)
        restored = load_state(session_id)

        assert restored.should_skip_collecting is True
        assert restored.should_skip_hardware is False
        assert restored.hallucination_check_result == "network_ok"
