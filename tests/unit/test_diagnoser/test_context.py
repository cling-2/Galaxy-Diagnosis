"""诊断上下文组装测试（REQ-C-01）

覆盖：关键词匹配、预处理、安全采集、被动接收、整体编排。
"""

from unittest.mock import patch

import pytest

from galaxy_diag.diagnoser import context
from galaxy_diag.diagnoser.context import (
    TOOL_COMPONENT,
    TOOL_LOGS,
    TOOL_NETWORK,
    TOOL_RESOURCES,
    build_diagnostic_context,
    extract_ping_targets,
    match_tools_by_keywords,
    preprocess_logs,
    should_collect_hardware,
)
from galaxy_diag.shared.errors import CollectorError, CollectorToolNotFoundError
from galaxy_diag.shared.types import (
    ContainerRuntime,
    EnvInfo,
    EnvironmentType,
    HardwareInfo,
    LogSnippet,
)


# ===== match_tools_by_keywords =====


class TestMatchToolsByKeywords:
    """关键词 → Tool 映射"""

    def test_network_keywords(self):
        """网络关键词 → collect_network_connectivity"""
        tools = match_tools_by_keywords("网络不通，ping 不通网关")
        assert TOOL_NETWORK in tools
        assert TOOL_RESOURCES in tools  # 兜底始终在

    def test_storage_keywords(self):
        """存储关键词 → collect_service_logs"""
        tools = match_tools_by_keywords("磁盘挂载失败，存储无法识别")
        assert TOOL_LOGS in tools

    def test_service_keywords(self):
        """服务关键词 → collect_component_status"""
        tools = match_tools_by_keywords("galaxy-compute 服务启动失败")
        assert TOOL_COMPONENT in tools

    def test_resource_keywords(self):
        """资源关键词 → collect_system_resources"""
        tools = match_tools_by_keywords("系统很慢，CPU 负载高")
        assert TOOL_RESOURCES in tools

    def test_no_match_falls_back_to_base_set(self):
        """无关键词命中 → 最小基础集（component + resources）"""
        tools = match_tools_by_keywords("随便看看")
        assert tools == {TOOL_COMPONENT, TOOL_RESOURCES}

    def test_empty_description_base_set(self):
        """空描述 → 最小基础集"""
        tools = match_tools_by_keywords("")
        assert tools == {TOOL_COMPONENT, TOOL_RESOURCES}

    def test_resources_always_collected(self):
        """resources 始终在集合中"""
        for desc in ["网络问题", "磁盘问题", "服务问题", ""]:
            assert TOOL_RESOURCES in match_tools_by_keywords(desc)

    def test_multiple_categories(self):
        """多类关键词同时命中"""
        tools = match_tools_by_keywords("网络不通且服务启动失败，磁盘也报错")
        assert TOOL_NETWORK in tools
        assert TOOL_COMPONENT in tools
        assert TOOL_LOGS in tools


# ===== preprocess_logs =====


class TestPreprocessLogs:
    """日志预处理"""

    def test_single_snippet_truncation(self):
        """单条超 2KB 截断"""
        snip = LogSnippet(source="test", level="Info", content="x" * 3000)
        result = preprocess_logs([snip])
        assert len(result[0].content) < 3000
        assert result[0].truncated is True
        assert "[truncated]" in result[0].content

    def test_priority_ordering(self):
        """ERROR 优先于 Warning 优先于 Info"""
        snippets = [
            LogSnippet(source="info", level="Info", content="info log"),
            LogSnippet(source="err", level="ERROR", content="error log"),
            LogSnippet(source="warn", level="Warning", content="warn log"),
        ]
        result = preprocess_logs(snippets)
        # ERROR 应排第一
        assert result[0].level == "ERROR"

    def test_budget_control_drops_low_priority(self):
        """超预算时丢弃低优先级"""
        # 构造超 32KB 的日志
        big_error = LogSnippet(source="err", level="ERROR", content="E" * 20000)
        big_info = LogSnippet(source="info", level="Info", content="I" * 20000)
        result = preprocess_logs([big_error, big_info], budget_kb=32)
        # ERROR 保留，Info 可能被丢弃
        assert any(s.level == "ERROR" for s in result)

    def test_empty_input(self):
        """空输入 → 空输出"""
        assert preprocess_logs([]) == []


# ===== build_diagnostic_context 编排 =====


class TestBuildContext:
    """顶层编排"""

    def _make_env_info(self, env_type=EnvironmentType.BARE_METAL, runtime=None):
        return EnvInfo(
            env_type=env_type,
            container_runtime=runtime,
            hardware=HardwareInfo(),
        )

    def test_full_orchestration(self):
        """完整编排：关键词匹配 → 采集 → 组装"""
        env_info = self._make_env_info()
        with patch("galaxy_diag.diagnoser.context.collect_component_status", return_value=[
            {"name": "galaxy-compute", "status": "running", "detail": "ok"},
        ]), \
             patch("galaxy_diag.diagnoser.context.collect_service_logs", return_value=[]), \
             patch("galaxy_diag.diagnoser.context.collect_system_resources", return_value={"cpu_load": "10%"}), \
             patch("galaxy_diag.diagnoser.context.collect_network_connectivity", return_value=[]):
            ctx = build_diagnostic_context("服务启动失败", env_info)

        assert ctx.problem_description == "服务启动失败"
        assert ctx.env_info_ref == EnvironmentType.BARE_METAL
        assert ctx.container_runtime is None
        assert len(ctx.component_status) == 1
        assert ctx.system_resources["cpu_load"] == "10%"
        assert TOOL_RESOURCES in ctx.collected_tools

    def test_partial_failure_degradation(self):
        """单项失败降级：component 失败，resources 成功"""
        env_info = self._make_env_info()
        with patch("galaxy_diag.diagnoser.context.collect_component_status",
                   side_effect=CollectorToolNotFoundError("systemctl 不可用")), \
             patch("galaxy_diag.diagnoser.context.collect_service_logs", return_value=[]), \
             patch("galaxy_diag.diagnoser.context.collect_system_resources", return_value={"cpu": "5%"}), \
             patch("galaxy_diag.diagnoser.context.collect_network_connectivity", return_value=[]):
            ctx = build_diagnostic_context("服务失败", env_info)

        assert ctx.component_status == []  # 降级为空
        assert any("collect_component_status" in w for w in ctx.collection_warnings)
        assert ctx.system_resources == {"cpu": "5%"}  # 其他不受影响

    def test_all_fail_raises(self):
        """所有 Tool 均失败 → 抛 CollectorError"""
        env_info = self._make_env_info()
        with patch("galaxy_diag.diagnoser.context.collect_component_status",
                   side_effect=CollectorToolNotFoundError("不可用")), \
             patch("galaxy_diag.diagnoser.context.collect_service_logs",
                   side_effect=CollectorToolNotFoundError("不可用")), \
             patch("galaxy_diag.diagnoser.context.collect_system_resources",
                   side_effect=CollectorToolNotFoundError("不可用")), \
             patch("galaxy_diag.diagnoser.context.collect_network_connectivity",
                   side_effect=CollectorToolNotFoundError("不可用")):
            with pytest.raises(CollectorError):
                build_diagnostic_context("服务失败", env_info)

    def test_user_log_files_loaded(self):
        """被动接收：用户日志文件"""
        env_info = self._make_env_info()
        with patch("galaxy_diag.diagnoser.context.collect_component_status", return_value=[
            {"name": "x", "status": "running", "detail": ""}]), \
             patch("galaxy_diag.diagnoser.context.collect_service_logs", return_value=[]), \
             patch("galaxy_diag.diagnoser.context.collect_system_resources", return_value={}), \
             patch("galaxy_diag.diagnoser.context.collect_network_connectivity", return_value=[]), \
             patch("galaxy_diag.diagnoser.context._load_user_logs",
                   return_value=["[user-upload:/tmp/test.log]\nlog content"]):
            ctx = build_diagnostic_context(
                "服务失败", env_info, user_log_files=["/tmp/test.log"],
            )
        assert len(ctx.user_provided) == 1
        assert "/tmp/test.log" in ctx.user_provided[0]

    def test_user_log_file_read_failure_warning(self):
        """用户日志读取失败 → warning，不阻断"""
        env_info = self._make_env_info()

        def fake_load_user_logs(files, warnings):
            # 模拟读取失败：返回空但写入 warning
            for path in files:
                warnings.append(f"用户日志文件读取失败: {path}（No such file）")
            return []

        with patch("galaxy_diag.diagnoser.context.collect_component_status", return_value=[
            {"name": "x", "status": "running", "detail": ""}]), \
             patch("galaxy_diag.diagnoser.context.collect_service_logs", return_value=[]), \
             patch("galaxy_diag.diagnoser.context.collect_system_resources", return_value={}), \
             patch("galaxy_diag.diagnoser.context.collect_network_connectivity", return_value=[]), \
             patch("galaxy_diag.diagnoser.context._load_user_logs",
                   side_effect=fake_load_user_logs):
            ctx = build_diagnostic_context(
                "服务失败", env_info, user_log_files=["/nonexistent/path.log"],
            )
        assert ctx.user_provided == []

    def test_raw_output_contains_summary(self):
        """raw_output 含摘要，日志用标记包裹"""
        env_info = self._make_env_info()
        snippet = LogSnippet(source="/var/log/test", level="ERROR", content="disk error")
        with patch("galaxy_diag.diagnoser.context.collect_component_status", return_value=[]), \
             patch("galaxy_diag.diagnoser.context.collect_service_logs", return_value=[snippet]), \
             patch("galaxy_diag.diagnoser.context.collect_system_resources", return_value={}), \
             patch("galaxy_diag.diagnoser.context.collect_network_connectivity", return_value=[]):
            ctx = build_diagnostic_context("磁盘报错", env_info)
        assert "log_snippets" in ctx.raw_output
        assert "<log" in ctx.raw_output["log_snippets"]

    def test_container_runtime_propagated(self):
        """容器运行时子类型传递到 context"""
        env_info = self._make_env_info(EnvironmentType.CONTAINER, ContainerRuntime.DOCKER)
        with patch("galaxy_diag.diagnoser.context.collect_component_status", return_value=[
            {"name": "x", "status": "running", "detail": ""}]), \
             patch("galaxy_diag.diagnoser.context.collect_service_logs", return_value=[]), \
             patch("galaxy_diag.diagnoser.context.collect_system_resources", return_value={}), \
             patch("galaxy_diag.diagnoser.context.collect_network_connectivity", return_value=[]):
            ctx = build_diagnostic_context("容器问题", env_info)
        assert ctx.container_runtime == ContainerRuntime.DOCKER
        assert ctx.env_info_ref == EnvironmentType.CONTAINER

    def test_all_fail_but_user_logs_ok(self):
        """所有 Tool 失败但有用户日志 → 不抛错（用户日志兜底）"""
        env_info = self._make_env_info()
        with patch("galaxy_diag.diagnoser.context.collect_component_status",
                   side_effect=CollectorToolNotFoundError("不可用")), \
             patch("galaxy_diag.diagnoser.context.collect_service_logs",
                   side_effect=CollectorToolNotFoundError("不可用")), \
             patch("galaxy_diag.diagnoser.context.collect_system_resources",
                   side_effect=CollectorToolNotFoundError("不可用")), \
             patch("galaxy_diag.diagnoser.context.collect_network_connectivity",
                   side_effect=CollectorToolNotFoundError("不可用")), \
             patch("galaxy_diag.diagnoser.context._load_user_logs",
                   return_value=["[user-upload:/tmp/test.log]\nlog content"]):
            ctx = build_diagnostic_context(
                "服务失败", env_info, user_log_files=["/tmp/test.log"],
            )
        assert len(ctx.user_provided) == 1


# ===== should_collect_hardware =====


class TestShouldCollectHardware:
    """C类：按需精简硬件采集的关键词判断"""

    def test_disk_problem_needs_hardware(self):
        from galaxy_diag.diagnoser.context import should_collect_hardware
        assert should_collect_hardware("磁盘 I/O error") is True

    def test_raid_firmware_needs_hardware(self):
        from galaxy_diag.diagnoser.context import should_collect_hardware
        assert should_collect_hardware("RAID 卡固件版本不兼容") is True

    def test_mount_problem_needs_hardware(self):
        from galaxy_diag.diagnoser.context import should_collect_hardware
        assert should_collect_hardware("挂载失败 mount error") is True

    def test_network_problem_no_hardware(self):
        from galaxy_diag.diagnoser.context import should_collect_hardware
        assert should_collect_hardware("容器间网络不通") is False

    def test_service_fail_no_hardware(self):
        from galaxy_diag.diagnoser.context import should_collect_hardware
        assert should_collect_hardware("服务启动失败") is False

    def test_oom_no_hardware(self):
        from galaxy_diag.diagnoser.context import should_collect_hardware
        assert should_collect_hardware("OOM 内存不足") is False

    def test_mixed_problem_needs_hardware(self):
        """同时命中需要和不需要的关键词，需要优先"""
        from galaxy_diag.diagnoser.context import should_collect_hardware
        assert should_collect_hardware("磁盘问题导致服务失败") is True

    def test_empty_defaults_to_collect(self):
        """空描述默认采集（保守）"""
        from galaxy_diag.diagnoser.context import should_collect_hardware
        assert should_collect_hardware("") is True

    def test_unrelated_defaults_to_collect(self):
        """无关描述默认采集（保守）"""
        from galaxy_diag.diagnoser.context import should_collect_hardware
        assert should_collect_hardware("something random xyz") is True

    def test_controller_needs_hardware(self):
        from galaxy_diag.diagnoser.context import should_collect_hardware
        assert should_collect_hardware("VM 数据盘控制器驱动未加载") is True

    def test_lsblk_needs_hardware(self):
        from galaxy_diag.diagnoser.context import should_collect_hardware
        assert should_collect_hardware("lsblk 只显示系统盘") is True

    def test_hdd_keyword_needs_hardware(self):
        """'硬盘' 命中 NEEDED 关键词 → True"""
        from galaxy_diag.diagnoser.context import should_collect_hardware
        assert should_collect_hardware("硬盘故障") is True

    def test_broad_pan_no_longer_false_positive(self):
        """收紧后 '盘' 不再匹配：'系统盘点异常' 不命中 NEEDED 也不命中 NOT_NEEDED → 默认 True（保守）"""
        from galaxy_diag.diagnoser.context import should_collect_hardware
        assert should_collect_hardware("系统盘点异常") is True

    def test_jianpan_no_longer_false_positive(self):
        """收紧后 '盘' 不再匹配：'键盘异常' 不命中 NEEDED 也不命中 NOT_NEEDED → 默认 True（保守）"""
        from galaxy_diag.diagnoser.context import should_collect_hardware
        # '键盘' doesn't match any NEEDED or NOT_NEEDED keyword → default True
        assert should_collect_hardware("键盘异常") is True


class TestExtractPingTargets:
    """从问题描述提取 IP/主机名作为 ping 目标"""

    def test_ipv4_extracted(self):
        assert extract_ping_targets("无法访问 10.0.1.100") == ["10.0.1.100"]

    def test_multiple_ips(self):
        result = extract_ping_targets("10.0.1.1 和 192.168.0.50 不通")
        assert result == ["10.0.1.1", "192.168.0.50"]

    def test_hostname_extracted(self):
        result = extract_ping_targets("无法连接 api.example.com")
        assert result == ["api.example.com"]

    def test_ip_and_hostname_mixed(self):
        result = extract_ping_targets("10.0.1.100 和 registry.k8s.io 都不通")
        assert "10.0.1.100" in result
        assert "registry.k8s.io" in result

    def test_no_target_returns_empty(self):
        assert extract_ping_targets("网络不通") == []
        assert extract_ping_targets("") == []

    def test_dedup(self):
        result = extract_ping_targets("10.0.1.1 和 10.0.1.1 重复")
        assert result == ["10.0.1.1"]

    def test_invalid_ipv4_filtered(self):
        """256.999.1.1 不是合法 IPv4"""
        assert extract_ping_targets("256.999.1.1 不通") == []

    def test_version_number_not_hostname(self):
        """纯版本号如 Python3.10 不应被当作主机名"""
        result = extract_ping_targets("Python3.10 环境")
        # 3.10 不含 TLD，不应被提取
        assert result == []
