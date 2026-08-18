"""D-03 安全检测循环测试（Cause A）

验证：
1. CRITICAL 失败后回退 PLANNING 时，失败反馈回灌给 LLM，第二次生成收敛通过。
2. 反复 CRITICAL 达到 MAX_SECURITY_RETRIES 时，安全终止（mark_done），
   绝不进入 EXECUTION_GUARD / EXECUTING（不执行被判失败的修复）。
"""

import json
from unittest.mock import MagicMock

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


_PLACEHOLDER_FIX = json.dumps({
    "steps": [
        {
            "command": "systemctl restart <SERVICE_NAME>",
            "description": "重启服务",
            "risk_note": "重启期间服务短暂中断",
            "parameters": {"SERVICE_NAME": "galaxy-network"},
            "is_verification": False,
        },
        {
            "command": "systemctl status <SERVICE_NAME>",
            "description": "验证服务状态",
            "risk_note": "只读操作，无风险",
            "parameters": {"SERVICE_NAME": "galaxy-network"},
            "is_verification": True,
        },
    ],
    "script_language": "bash",
    "risk_notes": ["重启期间服务不可用"],
    "impact_scope": "重启 galaxy-network 服务",
}, ensure_ascii=False)

_CLEAN_FIX = json.dumps({
    "steps": [
        {
            "command": "journalctl -u galaxy-api -n 100",
            "description": "查看服务日志",
            "risk_note": "只读操作，无风险",
            "parameters": {},
            "is_verification": False,
        },
        {
            "command": "systemctl status galaxy-api",
            "description": "验证服务状态",
            "risk_note": "只读操作，无风险",
            "parameters": {},
            "is_verification": True,
        },
    ],
    "script_language": "bash",
    "risk_notes": ["无额外风险"],
    "impact_scope": "仅查看状态与日志",
}, ensure_ascii=False)


class _ScriptedAdapter:
    """按脚本顺序返回预设 fix JSON，并捕获每次发送给 LLM 的消息"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.captured: list[list[dict]] = []

    def chat(self, messages, **kwargs):
        self.captured.append([dict(m) for m in messages])
        if not self._responses:
            raise AssertionError("fake adapter 耗尽：调用次数超出预期")
        return self._responses.pop(0)


def _make_state() -> WorkflowState:
    return WorkflowState(
        session_id="test_sec_loop",
        current_step=WorkflowStep.PLANNING,
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
            evidence=["systemctl status 显示 failed"],
            missing_info=[],
            env_type=EnvironmentType.CONTAINER,
            investigation_steps=[],
            fault_scope="服务层",
            diagnosis_source=DiagnosisSource.LLM,
        ),
    )


def _make_engine(adapter, state) -> WorkflowEngine:
    engine = WorkflowEngine(state, auto=True, mock=True)
    engine._model_adapter = adapter
    return engine


def _patch_interact(monkeypatch):
    """让交互式编辑不阻塞：占位符保持未替换（从而持续触发 CRITICAL）"""
    monkeypatch.setattr(
        "galaxy_diag.workflow.cli.interact.prompt_edit_params",
        lambda template, placeholders: {},
    )
    # REVIEWING 菜单输入 "n" 拒绝以干净退出
    monkeypatch.setattr("builtins.input", lambda *a, **kw: "n")


class TestSecurityLoopConverges:
    def test_loop_converges_with_feedback(self, monkeypatch):
        """第1次返回占位符 fix（CRITICAL）→ 反馈回灌 → 第2次返回干净 fix → 通过"""
        _patch_interact(monkeypatch)
        adapter = _ScriptedAdapter([_PLACEHOLDER_FIX, _CLEAN_FIX])
        engine = _make_engine(adapter, _make_state())

        engine.run()

        assert len(adapter.captured) == 2
        # 第二次调用的消息应包含失败反馈
        last_user = [m for m in adapter.captured[1] if m["role"] == "user"][-1]
        assert "上次生成未通过检测" in last_user["content"]
        # 引擎应推进到 EXECUTION_GUARD（说明第二次通过了 D-03）
        steps = [h.get("step") for h in engine.state.history]
        assert "execution_guard" in steps
        assert "reviewing" in steps  # EXECUTION_GUARD 后进入 REVIEWING，input "n" 拒绝
        # 修复建议通过检测
        assert engine.state.fix.check_passed is True


class TestSecurityLoopExhaustion:
    def test_loop_exhaustion_terminates_safely(self, monkeypatch):
        """反复 CRITICAL → 达上限安全终止，绝不执行"""
        _patch_interact(monkeypatch)
        adapter = _ScriptedAdapter([_PLACEHOLDER_FIX] * 5)  # 充足的失败响应
        engine = _make_engine(adapter, _make_state())

        engine.run()

        # MAX_SECURITY_RETRIES=2 → 初次 + 2 次重试 = 3 次生成
        assert len(adapter.captured) == 3
        # 会话标记为 done（终止）
        last = engine.state.history[-1]
        assert last.get("result") == "done"
        # 绝未进入执行链路
        steps = [h.get("step") for h in engine.state.history]
        assert "execution_guard" not in steps
        assert "executing" not in steps
        assert "snapshot" not in steps
