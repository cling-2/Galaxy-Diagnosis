"""规则匹配引擎测试"""

from unittest.mock import patch

import pytest

from galaxy_diag.diagnoser.rules import (
    DIAGNOSIS_RULES,
    DiagnosisRule,
    _concat_context_text,
    match_rules,
)
from galaxy_diag.shared.types import (
    Confidence,
    DiagnosisSource,
    DiagnosticContext,
    EnvironmentType,
    LogSnippet,
)


def _make_ctx(
    problem_description: str = "",
    env_type: str = "bare_metal",
    component_status: list[dict] | None = None,
    log_content: str = "",
    network_checks: list[dict] | None = None,
    system_resources: dict | None = None,
) -> DiagnosticContext:
    """构造测试用 DiagnosticContext"""
    log_snippets = []
    if log_content:
        log_snippets = [LogSnippet(source="test", level="ERROR", content=log_content)]

    return DiagnosticContext(
        problem_description=problem_description,
        env_info_ref=env_type,
        component_status=component_status or [],
        log_snippets=log_snippets,
        system_resources=system_resources or {},
        network_checks=network_checks or [],
    )


class TestDiagnosisRule:
    def test_construction(self):
        rule = DiagnosisRule(
            rule_id="test",
            description="test rule",
            env_types=[EnvironmentType.CONTAINER],
            match_conditions=["kubelet", "failed"],
            root_cause="test cause",
            confidence=Confidence.SUSPECTED,
            evidence_template=["evidence"],
            investigation_steps=["step1"],
            fault_scope="scope",
        )
        assert rule.rule_id == "test"
        assert rule.env_types == [EnvironmentType.CONTAINER]

    def test_empty_env_types_means_all_envs(self):
        rule = DiagnosisRule(
            rule_id="test",
            description="",
            env_types=[],
            match_conditions=[],
            root_cause="",
            confidence=Confidence.CONFIRMED,
            evidence_template=[],
            investigation_steps=[],
            fault_scope="",
        )
        assert rule.env_types == []


class TestConcatContextText:
    def test_includes_problem_description(self):
        ctx = _make_ctx(problem_description="network unreachable")
        text = _concat_context_text(ctx).lower()
        assert "network unreachable" in text

    def test_includes_component_status(self):
        ctx = _make_ctx(component_status=[{"name": "kubelet", "status": "failed", "detail": ""}])
        text = _concat_context_text(ctx).lower()
        assert "kubelet" in text
        assert "failed" in text

    def test_includes_log_content(self):
        ctx = _make_ctx(log_content="stale file handle at /data/nfs")
        text = _concat_context_text(ctx).lower()
        assert "stale file handle" in text

    def test_includes_network_checks(self):
        ctx = _make_ctx(network_checks=[{"target": "10.0.1.100", "reachable": False, "detail": "unreachable"}])
        text = _concat_context_text(ctx).lower()
        assert "unreachable" in text


class TestMatchRules:
    def test_container_kubelet_down(self):
        ctx = _make_ctx(
            env_type="container",
            component_status=[{"name": "kubelet", "status": "failed", "detail": ""}],
        )
        result = match_rules(ctx)
        assert result is not None
        assert result.root_cause == "Kubelet 服务未运行，容器编排异常"
        assert result.confidence == Confidence.SUSPECTED
        assert result.diagnosis_source == DiagnosisSource.RULE_MATCH

    def test_container_kubelet_down_not_matching_bare_metal(self):
        """container_kubelet_down 只匹配容器环境"""
        ctx = _make_ctx(
            env_type="bare_metal",
            component_status=[{"name": "kubelet", "status": "failed", "detail": ""}],
        )
        result = match_rules(ctx)
        # bare_metal 不匹配容器特定规则，但可能匹配 service_start_fail
        assert result is None or result.root_cause != "Kubelet 服务未运行，容器编排异常"

    def test_container_pod_crashloop(self):
        ctx = _make_ctx(
            env_type="container",
            problem_description="Pod CrashLoopBackOff",
        )
        result = match_rules(ctx)
        assert result is not None
        assert result.confidence == Confidence.CONFIRMED
        assert "CrashLoopBackOff" in result.root_cause or "崩溃循环" in result.root_cause

    def test_storage_nfs_stale(self):
        ctx = _make_ctx(log_content="stale file handle nfs mount error")
        result = match_rules(ctx)
        assert result is not None
        assert "NFS" in result.root_cause
        assert result.confidence == Confidence.CONFIRMED

    def test_network_unreachable(self):
        ctx = _make_ctx(network_checks=[{"target": "10.0.1.100", "reachable": False, "detail": "unreachable"}])
        result = match_rules(ctx)
        assert result is not None
        assert "不可达" in result.root_cause

    def test_resource_oom(self):
        ctx = _make_ctx(log_content="Out of memory OOM killed process")
        result = match_rules(ctx)
        assert result is not None
        assert "OOM" in result.root_cause

    def test_disk_io_error(self):
        ctx = _make_ctx(log_content="I/O error dev sda sector 12345")
        result = match_rules(ctx)
        assert result is not None
        assert "I/O" in result.root_cause

    def test_no_match_returns_none(self):
        ctx = _make_ctx(problem_description="something completely unrelated xyz123")
        result = match_rules(ctx)
        assert result is None

    def test_partial_condition_no_match(self):
        """AND 逻辑：仅匹配部分关键词不命中"""
        ctx = _make_ctx(
            env_type="container",
            component_status=[{"name": "kubelet", "status": "running", "detail": ""}],
        )
        # 只有 "kubelet" 没有 "failed"，不应匹配 container_kubelet_down
        result = match_rules(ctx)
        assert result is None or "Kubelet" not in result.root_cause

    def test_investigation_steps_populated(self):
        ctx = _make_ctx(log_content="Out of memory OOM killed process")
        result = match_rules(ctx)
        assert result is not None
        assert len(result.investigation_steps) > 0

    def test_fault_scope_populated(self):
        ctx = _make_ctx(log_content="Out of memory OOM killed process")
        result = match_rules(ctx)
        assert result is not None
        assert result.fault_scope != ""

    def test_env_specific_rule_preferred(self):
        """环境特定规则优先于通用规则"""
        # 容器环境 + "failed" → 应该匹配 container_kubelet_down 而非 service_start_fail
        ctx = _make_ctx(
            env_type="container",
            component_status=[{"name": "kubelet", "status": "failed", "detail": ""}],
        )
        result = match_rules(ctx)
        assert result is not None
        assert result.rule_id if hasattr(result, 'rule_id') else True
        # container_kubelet_down 是容器特定规则，应优先
        assert "Kubelet" in result.root_cause


class TestPrematchRulesByDescription:
    """B 类：prematch_rules_by_description 规则预匹配（决定是否跳过 COLLECTING）"""

    def test_crashloop_confirmed_match(self):
        """Pod CrashLoopBackOff → CONFIRMED → 返回 DiagnosisResult"""
        from galaxy_diag.diagnoser.rules import prematch_rules_by_description
        result = prematch_rules_by_description(
            "Pod 出现 CrashLoopBackOff",
            EnvironmentType.CONTAINER,
        )
        assert result is not None
        assert result.confidence == Confidence.CONFIRMED

    def test_oom_confirmed_match(self):
        """OOM → CONFIRMED → 返回 DiagnosisResult"""
        from galaxy_diag.diagnoser.rules import prematch_rules_by_description
        result = prematch_rules_by_description(
            "Out of memory OOM killed",
            EnvironmentType.BARE_METAL,
        )
        assert result is not None
        assert result.confidence == Confidence.CONFIRMED

    def test_container_rule_not_matching_bare_metal(self):
        """容器特定规则在裸金属环境不匹配"""
        from galaxy_diag.diagnoser.rules import prematch_rules_by_description
        result = prematch_rules_by_description(
            "CrashLoopBackOff",
            EnvironmentType.BARE_METAL,
        )
        assert result is None

    def test_suspected_not_returned(self):
        """SUSPECTED 规则不触发预匹配（保守）"""
        from galaxy_diag.diagnoser.rules import prematch_rules_by_description
        # "unreachable" 匹配 network_unreachable（SUSPECTED）
        result = prematch_rules_by_description(
            "目标 unreachable",
            EnvironmentType.BARE_METAL,
        )
        # SUSPECTED 不触发 B 类跳过
        assert result is None or result.confidence != Confidence.SUSPECTED

    def test_no_match_returns_none(self):
        from galaxy_diag.diagnoser.rules import prematch_rules_by_description
        result = prematch_rules_by_description(
            "something unrelated xyz",
            EnvironmentType.BARE_METAL,
        )
        assert result is None

    def test_io_error_confirmed_match(self):
        """I/O error → CONFIRMED"""
        from galaxy_diag.diagnoser.rules import prematch_rules_by_description
        result = prematch_rules_by_description(
            "磁盘 I/O error",
            EnvironmentType.BARE_METAL,
        )
        assert result is not None
        assert result.confidence == Confidence.CONFIRMED

    def test_nfs_stale_confirmed_match(self):
        """stale file handle + nfs → CONFIRMED"""
        from galaxy_diag.diagnoser.rules import prematch_rules_by_description
        result = prematch_rules_by_description(
            "nfs stale file handle",
            EnvironmentType.VM,
        )
        assert result is not None
        assert result.confidence == Confidence.CONFIRMED
