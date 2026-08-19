"""反幻觉事实校验测试"""

from galaxy_diag.diagnoser.hallucination_guard import check_facts, HallucinationCheckResult
from galaxy_diag.shared.types import DiagnosticContext, LogSnippet


def _make_ctx(
    problem_description: str = "",
    network_checks: list[dict] | None = None,
    component_status: list[dict] | None = None,
    log_snippets: list[LogSnippet] | None = None,
    system_resources: dict | None = None,
) -> DiagnosticContext:
    return DiagnosticContext(
        problem_description=problem_description,
        component_status=component_status or [],
        log_snippets=log_snippets or [],
        system_resources=system_resources or {},
        network_checks=network_checks or [],
    )


class TestCheckFacts:
    def test_network_ok_when_all_reachable(self):
        """网络问题但所有目标可达 → 矛盾"""
        ctx = _make_ctx(
            problem_description="容器间网络不通 ping不通",
            network_checks=[
                {"target": "10.0.1.100", "reachable": True, "detail": "ok"},
                {"target": "10.0.1.101", "reachable": True, "detail": "ok"},
            ],
        )
        result = check_facts("容器间网络不通", ctx)
        assert result is not None
        assert result.contradiction is True
        assert "网络" in result.message

    def test_network_not_ok_when_unreachable(self):
        """网络问题且有不可达目标 → 不矛盾"""
        ctx = _make_ctx(
            problem_description="网络不通",
            network_checks=[
                {"target": "10.0.1.100", "reachable": False, "detail": "unreachable"},
            ],
        )
        result = check_facts("网络不通", ctx)
        # 无矛盾（或 result 为 None，即无匹配的校验规则认为矛盾）
        if result is not None:
            assert result.contradiction is False

    def test_service_ok_when_all_running(self):
        """服务启动失败但所有服务运行中 → 矛盾"""
        ctx = _make_ctx(
            problem_description="服务启动失败",
            component_status=[
                {"name": "galaxy-api", "status": "running", "detail": ""},
                {"name": "galaxy-scheduler", "status": "running", "detail": ""},
            ],
        )
        result = check_facts("服务启动失败", ctx)
        assert result is not None
        assert result.contradiction is True
        assert "服务" in result.message

    def test_service_not_ok_when_failed_exists(self):
        """服务启动失败且有 failed 组件 → 不矛盾"""
        ctx = _make_ctx(
            problem_description="服务启动失败",
            component_status=[
                {"name": "galaxy-api", "status": "failed", "detail": "exit code 1"},
            ],
        )
        result = check_facts("服务启动失败", ctx)
        if result is not None:
            assert result.contradiction is False

    def test_mount_ok_when_no_error_in_logs(self):
        """挂载问题但日志无 mount error → 矛盾"""
        ctx = _make_ctx(
            problem_description="挂载失败",
            log_snippets=[
                LogSnippet(source="dmesg", level="Info", content="normal boot"),
            ],
        )
        result = check_facts("挂载失败", ctx)
        assert result is not None
        assert result.contradiction is True
        assert "挂载" in result.message

    def test_mount_not_ok_when_error_in_logs(self):
        """挂载问题且日志有 mount error → 不矛盾"""
        ctx = _make_ctx(
            problem_description="挂载失败",
            log_snippets=[
                LogSnippet(source="dmesg", level="ERROR", content="mount error(13): Permission denied"),
            ],
        )
        result = check_facts("挂载失败", ctx)
        if result is not None:
            assert result.contradiction is False

    def test_oom_ok_when_memory_sufficient(self):
        """OOM 问题但内存充足 → 矛盾"""
        ctx = _make_ctx(
            problem_description="OOM 内存不足",
            system_resources={"mem_used_percent": "45.2", "oom_count": "0"},
        )
        result = check_facts("OOM 内存不足", ctx)
        assert result is not None
        assert result.contradiction is True
        assert "内存" in result.message

    def test_oom_not_ok_when_memory_high(self):
        """OOM 问题且内存紧张 → 不矛盾"""
        ctx = _make_ctx(
            problem_description="OOM",
            system_resources={"mem_used_percent": "95.8", "oom_count": "3"},
        )
        result = check_facts("OOM", ctx)
        if result is not None:
            assert result.contradiction is False

    def test_unrelated_problem_no_match(self):
        """无关问题 → 无校验规则匹配 → None"""
        ctx = _make_ctx(problem_description="磁盘 I/O error")
        result = check_facts("磁盘 I/O error", ctx)
        assert result is None

    def test_short_circuit_prioritizes_later_contradiction(self):
        """前置规则不矛盾、后置规则矛盾 → 优先返回矛盾（短路优先级）

        场景：用户描述同时含网络与服务关键词；网络有不可达目标（不矛盾），
        但所有服务运行正常（矛盾）。旧逻辑会在网络规则返回 False 时短路返回，
        漏掉后续服务规则的矛盾 —— 反幻觉护栏绝不可漏报矛盾。
        """
        ctx = _make_ctx(
            problem_description="服务启动失败且网络不通",
            network_checks=[
                {"target": "10.0.1.100", "reachable": False, "detail": "unreachable"},
            ],
            component_status=[
                {"name": "galaxy-api", "status": "running", "detail": ""},
                {"name": "galaxy-scheduler", "status": "running", "detail": ""},
            ],
        )
        result = check_facts("服务启动失败且网络不通", ctx)
        assert result is not None
        assert result.contradiction is True
        assert result.rule_id == "service_ok"
        assert "服务" in result.message

    def test_empty_network_checks_returns_none(self):
        """网络问题但无网络检测结果 → None（无法判断，非不矛盾）"""
        ctx = _make_ctx(problem_description="网络不通")
        result = check_facts("网络不通", ctx)
        # 无网络采集数据，无法判断 → None
        assert result is None

    def test_startup_slow_no_false_contradiction(self):
        """收紧后 '启动' 单独不再匹配服务校验：'系统启动缓慢' + 全 running → None"""
        ctx = _make_ctx(
            problem_description="系统启动缓慢",
            component_status=[
                {"name": "galaxy-api", "status": "running", "detail": ""},
            ],
        )
        result = check_facts("系统启动缓慢", ctx)
        assert result is None

    def test_failover_no_false_contradiction(self):
        """收紧后 'fail' 单独不再匹配服务校验：'数据库failover异常' + 全 running → None"""
        ctx = _make_ctx(
            problem_description="数据库failover异常",
            component_status=[
                {"name": "galaxy-api", "status": "running", "detail": ""},
            ],
        )
        result = check_facts("数据库failover异常", ctx)
        assert result is None

    def test_resource_missing_keys_returns_none(self):
        """_check_resource_ok: 非空 resources 但缺少 mem_used_percent/oom_count → None"""
        ctx = _make_ctx(
            problem_description="OOM 内存不足",
            system_resources={"cpu_load": "5.2"},  # missing mem_used_percent & oom_count
        )
        result = check_facts("OOM 内存不足", ctx)
        assert result is None
