"""safety/verifier.py + fixer/agent._ensure_verification_step 测试

覆盖：
- verifier.verify(): 全部通过 / 部分失败 / 无验证命令 / 超时 / dry_run
- _ensure_verification_step(): LLM 已生成 / 未生成（各环境类型兜底）
"""

from __future__ import annotations

import pytest

from galaxy_diag.shared.types import (
    CommandTemplate,
    ContainerRuntime,
    DiagnosisResult,
    EnvInfo,
    EnvironmentType,
    FixProposal,
    FixSource,
    FixStep,
    FixSuggestion,
    VerifyResult,
)


# ===== verifier.verify() 测试 =====


def _make_proposal(*, with_verify: bool = True, verify_cmds: list[str] | None = None) -> FixProposal:
    """构造测试用 FixProposal"""
    commands = [
        CommandTemplate(command="echo fix", description="修复步骤", risk_note="无", is_verification=False),
    ]
    if with_verify:
        if verify_cmds:
            for cmd in verify_cmds:
                commands.append(CommandTemplate(command=cmd, description=f"验证: {cmd}", risk_note="无", is_verification=True))
        else:
            commands.append(CommandTemplate(command="echo ok", description="验证", risk_note="无", is_verification=True))
    return FixProposal(commands=commands, source=FixSource.LLM)


class TestVerifierSuccess:
    """验证全部通过"""

    def test_all_pass(self):
        from galaxy_diag.safety.verifier import verify

        proposal = _make_proposal(verify_cmds=["echo ok", "true"])
        result = verify(proposal)
        assert result.success is True
        assert result.total_steps == 2
        assert result.passed_steps == 2
        assert result.failed_step == -1

    def test_single_pass(self):
        from galaxy_diag.safety.verifier import verify

        proposal = _make_proposal(verify_cmds=["echo pass"])
        result = verify(proposal)
        assert result.success is True
        assert result.total_steps == 1
        assert result.passed_steps == 1


class TestVerifierFailure:
    """验证部分失败"""

    def test_first_fails(self):
        from galaxy_diag.safety.verifier import verify

        proposal = _make_proposal(verify_cmds=["false", "echo ok"])
        result = verify(proposal)
        assert result.success is False
        assert result.failed_step == 1
        assert result.failed_description == "验证: false"
        assert result.total_steps == 2
        assert result.passed_steps == 0

    def test_second_fails(self):
        from galaxy_diag.safety.verifier import verify

        proposal = _make_proposal(verify_cmds=["echo ok", "false"])
        result = verify(proposal)
        assert result.success is False
        assert result.failed_step == 2
        assert result.passed_steps == 1


class TestVerifierNoVerifyCommands:
    """无验证命令"""

    def test_no_verify_commands(self):
        from galaxy_diag.safety.verifier import verify

        proposal = _make_proposal(with_verify=False)
        result = verify(proposal)
        assert result.success is True
        assert result.total_steps == 0
        assert "无验证命令" in result.output


class TestVerifierDryRun:
    """dry_run 模式"""

    def test_dry_run(self):
        from galaxy_diag.safety.verifier import verify

        proposal = _make_proposal(verify_cmds=["false"])  # 本应失败
        result = verify(proposal, dry_run=True)
        assert result.success is True  # dry-run 不实际执行，视为通过
        assert result.total_steps == 1
        assert result.passed_steps == 1


class TestVerifierTimeout:
    """验证命令超时"""

    def test_timeout(self, monkeypatch):
        from galaxy_diag.safety import verifier

        # 缩短超时避免测试过慢
        monkeypatch.setattr(verifier, "_VERIFY_TIMEOUT", 2)
        # sleep 10 会超时（超时设为 2s）
        proposal = _make_proposal(verify_cmds=["sleep 10"])
        result = verifier.verify(proposal)
        assert result.success is False
        assert result.failed_step == 1


# ===== _ensure_verification_step 测试 =====


def _make_suggestion(*, with_verify: bool = True) -> FixSuggestion:
    """构造测试用 FixSuggestion"""
    steps = [
        FixStep(command="echo fix", description="修复步骤", risk_note="无", is_verification=False),
    ]
    if with_verify:
        steps.append(FixStep(command="echo ok", description="验证", risk_note="无", is_verification=True))
    return FixSuggestion(steps=steps, source=FixSource.LLM)


def _make_env_info(env_type: EnvironmentType = EnvironmentType.BARE_METAL,
                   container_runtime: ContainerRuntime | None = None,
                   has_docker_cli: bool = False,
                   has_kubectl_cli: bool = False) -> EnvInfo:
    return EnvInfo(env_type=env_type, container_runtime=container_runtime,
                   has_docker_cli=has_docker_cli, has_kubectl_cli=has_kubectl_cli)


def _make_diagnosis(fault_scope: str = "") -> DiagnosisResult:
    return DiagnosisResult(root_cause="test", fault_scope=fault_scope)


class TestEnsureVerificationStep:
    """_ensure_verification_step 测试"""

    def test_already_has_verify(self):
        from galaxy_diag.fixer.agent import _ensure_verification_step

        suggestion = _make_suggestion(with_verify=True)
        env_info = _make_env_info()
        diagnosis = _make_diagnosis()

        result = _ensure_verification_step(suggestion, env_info, diagnosis)
        assert any(s.is_verification for s in result.steps)
        assert result.source == FixSource.LLM  # 不改为 FALLBACK
        # 不应额外增加步骤
        verify_count = sum(1 for s in result.steps if s.is_verification)
        assert verify_count == 1

    def test_no_verify_adds_fallback_bare_metal(self):
        from galaxy_diag.fixer.agent import _ensure_verification_step

        suggestion = _make_suggestion(with_verify=False)
        env_info = _make_env_info(EnvironmentType.BARE_METAL)
        diagnosis = _make_diagnosis(fault_scope="系统层：配置异常")

        result = _ensure_verification_step(suggestion, env_info, diagnosis)
        assert any(s.is_verification for s in result.steps)
        assert result.source == FixSource.LLM_FALLBACK
        # 新增步骤应为验证命令
        last = result.steps[-1]
        assert last.is_verification is True
        assert "systemctl" in last.command

    def test_no_verify_adds_fallback_vm_disk(self):
        from galaxy_diag.fixer.agent import _ensure_verification_step

        suggestion = _make_suggestion(with_verify=False)
        env_info = _make_env_info(EnvironmentType.VM)
        diagnosis = _make_diagnosis(fault_scope="存储层：磁盘控制器驱动")

        result = _ensure_verification_step(suggestion, env_info, diagnosis)
        last = result.steps[-1]
        assert last.is_verification is True
        assert "lsblk" in last.command

    def test_no_verify_adds_fallback_vm_network(self):
        from galaxy_diag.fixer.agent import _ensure_verification_step

        suggestion = _make_suggestion(with_verify=False)
        env_info = _make_env_info(EnvironmentType.VM)
        diagnosis = _make_diagnosis(fault_scope="网络层：服务配置异常")

        result = _ensure_verification_step(suggestion, env_info, diagnosis)
        last = result.steps[-1]
        assert last.is_verification is True
        assert "ss" in last.command

    def test_no_verify_adds_fallback_container_k8s(self):
        from galaxy_diag.fixer.agent import _ensure_verification_step

        suggestion = _make_suggestion(with_verify=False)
        env_info = _make_env_info(EnvironmentType.CONTAINER, ContainerRuntime.KUBERNETES, has_kubectl_cli=True)
        diagnosis = _make_diagnosis()

        result = _ensure_verification_step(suggestion, env_info, diagnosis)
        last = result.steps[-1]
        assert last.is_verification is True
        assert "kubectl" in last.command

    def test_no_verify_adds_fallback_container_docker(self):
        from galaxy_diag.fixer.agent import _ensure_verification_step

        suggestion = _make_suggestion(with_verify=False)
        env_info = _make_env_info(EnvironmentType.CONTAINER, ContainerRuntime.DOCKER, has_docker_cli=True)
        diagnosis = _make_diagnosis()

        result = _ensure_verification_step(suggestion, env_info, diagnosis)
        last = result.steps[-1]
        assert last.is_verification is True
        assert "docker ps" in last.command

    def test_no_verify_adds_fallback_container_no_cli(self):
        """容器内无 docker/kubectl CLI → 用 ps 兜底验证"""
        from galaxy_diag.fixer.agent import _ensure_verification_step

        suggestion = _make_suggestion(with_verify=False)
        env_info = _make_env_info(EnvironmentType.CONTAINER, ContainerRuntime.DOCKER,
                                  has_docker_cli=False, has_kubectl_cli=False)
        diagnosis = _make_diagnosis()

        result = _ensure_verification_step(suggestion, env_info, diagnosis)
        last = result.steps[-1]
        assert last.is_verification is True
        assert "ps aux" in last.command
