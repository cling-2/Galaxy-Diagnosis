"""WorkflowState 扩展字段 + 状态转换测试"""
import pytest
from galaxy_diag.shared.types import WorkflowState, WorkflowStep
from galaxy_diag.workflow.states import (
    TRANSITIONS, SKIP_TARGETS, is_valid_transition,
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
        assert WorkflowStep.PLANNING in TRANSITIONS[WorkflowStep.ENV_RECOGNISING]

    def test_env_recognising_skip_target_includes_planning(self):
        """SKIP_TARGETS 包含 ENV_RECOGNISING → PLANNING"""
        assert WorkflowStep.PLANNING in SKIP_TARGETS.get(WorkflowStep.ENV_RECOGNISING, [])

    def test_env_recognising_to_collecting_still_valid(self):
        """原路径 ENV_RECOGNISING → COLLECTING 仍有效"""
        assert WorkflowStep.COLLECTING in TRANSITIONS[WorkflowStep.ENV_RECOGNISING]
