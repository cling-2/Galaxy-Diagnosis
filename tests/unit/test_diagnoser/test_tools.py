"""诊断采集 Tool 测试（REQ-C-01）

覆盖 4 个 Tool 的环境差异化采集与降级逻辑。
"""

from unittest.mock import patch

import pytest

from galaxy_diag.shared.errors import CollectorToolNotFoundError
from galaxy_diag.shared.types import (
    ContainerRuntime,
    EnvironmentType,
    LogSnippet,
)
from galaxy_diag.diagnoser import tools


# ===== collect_component_status =====


class TestCollectComponentStatus:
    """组件状态采集"""

    def test_bare_metal_systemctl(self):
        """裸金属：systemctl is-active"""
        with patch("galaxy_diag.diagnoser.tools._run_cmd") as mock_run, \
             patch("galaxy_diag.diagnoser.tools.shutil.which", return_value="/usr/bin/systemctl"):
            mock_run.return_value = (0, "active\nfailed\ninactive\n", "")
            result = tools.collect_component_status(
                EnvironmentType.BARE_METAL, None,
                ["galaxy-compute", "galaxy-network", "galaxy-storage"],
            )
        assert len(result) == 3
        assert result[0]["status"] == "running"
        assert result[1]["status"] == "failed"
        assert result[2]["status"] == "inactive"

    def test_k8s_kubectl(self):
        """K8s：kubectl get pods"""
        with patch("galaxy_diag.diagnoser.tools._run_cmd") as mock_run, \
             patch("galaxy_diag.diagnoser.tools.shutil.which", return_value="/usr/bin/kubectl"):
            mock_run.return_value = (0, "NAMESPACE NAME READY\ndefault galaxy-compute-xxx 1/1\n", "")
            result = tools.collect_component_status(
                EnvironmentType.CONTAINER, ContainerRuntime.KUBERNETES,
                ["galaxy-compute", "galaxy-api"],
            )
        assert result[0]["status"] == "running"
        assert result[1]["status"] == "inactive"

    def test_docker(self):
        """Docker：docker ps"""
        with patch("galaxy_diag.diagnoser.tools._run_cmd") as mock_run, \
             patch("galaxy_diag.diagnoser.tools.shutil.which", return_value="/usr/bin/docker"):
            # docker ps --format {{.Names}}\t{{.Status}} 输出
            mock_run.return_value = (0, "galaxy-compute-node\tUp 2 hours\ngalaxy-api-xxx\tExited (1)\n", "")
            result = tools.collect_component_status(
                EnvironmentType.CONTAINER, ContainerRuntime.DOCKER,
                ["galaxy-compute", "galaxy-api"],
            )
        # galaxy-compute 匹配容器 galaxy-compute-node → running
        assert result[0]["status"] == "running"
        # galaxy-api 匹配 galaxy-api-xxx，状态 Exited → failed
        assert result[1]["status"] == "failed"

    def test_tool_not_found(self):
        """命令不存在 → CollectorToolNotFoundError"""
        with patch("galaxy_diag.diagnoser.tools.shutil.which", return_value=None):
            with pytest.raises(CollectorToolNotFoundError):
                tools.collect_component_status(
                    EnvironmentType.BARE_METAL, None, ["galaxy-compute"],
                )

    def test_unknown_runtime_dual_path(self):
        """UNKNOWN 运行时：双路尝试，kubectl 失败则试 docker"""
        with patch("galaxy_diag.diagnoser.tools.shutil.which", return_value="/usr/bin/docker"), \
             patch("galaxy_diag.diagnoser.tools._run_cmd") as mock_run:
            # kubectl 调用抛异常，docker 调用成功
            mock_run.side_effect = [
                CollectorToolNotFoundError("kubectl 不可用"),
                (0, "galaxy-compute-node\tUp 2 hours\n", ""),  # docker ps
            ]
            result = tools.collect_component_status(
                EnvironmentType.CONTAINER, ContainerRuntime.UNKNOWN,
                ["galaxy-compute"],
            )
        assert len(result) == 1
        assert result[0]["status"] == "running"


# ===== collect_service_logs =====


class TestCollectServiceLogs:
    """服务日志采集"""

    def test_file_read_with_keyword_filter(self):
        """文件日志按关键词过滤"""
        log_content = (
            "INFO starting up\n"
            "ERROR disk mount failed\n"
            "INFO healthy\n"
            "Warning low memory\n"
        )
        with patch("galaxy_diag.diagnoser.tools._read_file", return_value=log_content):
            result = tools.collect_service_logs(
                EnvironmentType.BARE_METAL, None,
                {"test": "/var/log/test.log"},
                ["disk", "memory"],
            )
        assert len(result) == 1
        assert "ERROR" in result[0].level or "Warning" in result[0].level
        assert "disk mount failed" in result[0].content

    def test_file_not_found_skipped(self):
        """文件不存在 → 跳过"""
        with patch("galaxy_diag.diagnoser.tools._read_file", return_value=None):
            result = tools.collect_service_logs(
                EnvironmentType.BARE_METAL, None,
                {"test": "/var/log/test.log"},
                ["error"],
            )
        assert result == []

    def test_level_detection(self):
        """日志级别判定：ERROR > Warning > Info"""
        content = "Warning something\nERROR critical\n"
        with patch("galaxy_diag.diagnoser.tools._read_file", return_value=content):
            result = tools.collect_service_logs(
                EnvironmentType.BARE_METAL, None,
                {"test": "/var/log/test.log"},
                {"error", "warning"},
            )
        assert result[0].level == "ERROR"  # 最高级


# ===== collect_system_resources =====


class TestCollectSystemResources:
    """系统资源采集"""

    def test_loadavg_and_meminfo(self):
        """/proc/loadavg + /proc/meminfo 解析"""
        loadavg = "0.50 0.45 0.30 2/100 1234\n"
        meminfo = (
            "MemTotal:       16384000 kB\n"
            "MemAvailable:    8192000 kB\n"
        )
        with patch("galaxy_diag.diagnoser.tools._read_file", side_effect=[loadavg, meminfo]), \
             patch("galaxy_diag.diagnoser.tools._run_cmd", return_value=(0, "df output", "")), \
             patch("galaxy_diag.diagnoser.tools.shutil.which", return_value="/usr/bin/df"):
            result = tools.collect_system_resources()
        assert "load_avg" in result
        assert result["mem_total_gb"] == 15.6
        assert result["mem_used_gb"] == 7.8

    def test_no_proc_files(self):
        """/proc 不可读 → 返回部分结果"""
        with patch("galaxy_diag.diagnoser.tools._read_file", return_value=None), \
             patch("galaxy_diag.diagnoser.tools._run_cmd", return_value=(0, "df", "")), \
             patch("galaxy_diag.diagnoser.tools.shutil.which", return_value="/usr/bin/df"):
            result = tools.collect_system_resources()
        assert "disk_usage" in result
        assert "load_avg" not in result


# ===== collect_network_connectivity =====


class TestCollectNetworkConnectivity:
    """网络连通性采集"""

    def test_ping_reachable(self):
        """ping 可达"""
        with patch("galaxy_diag.diagnoser.tools._run_cmd", return_value=(0, "", "")), \
             patch("galaxy_diag.diagnoser.tools.shutil.which", return_value="/usr/bin/ping"), \
             patch("galaxy_diag.diagnoser.tools._collect_iptables", return_value=""):
            result = tools.collect_network_connectivity(
                EnvironmentType.BARE_METAL, None, ["192.168.1.1"],
            )
        assert result[0]["target"] == "192.168.1.1"
        assert result[0]["reachable"] is True

    def test_ping_unreachable(self):
        """ping 不可达"""
        with patch("galaxy_diag.diagnoser.tools._run_cmd", return_value=(1, "", "100% packet loss")), \
             patch("galaxy_diag.diagnoser.tools.shutil.which", return_value="/usr/bin/ping"), \
             patch("galaxy_diag.diagnoser.tools._collect_iptables", return_value=""):
            result = tools.collect_network_connectivity(
                EnvironmentType.BARE_METAL, None, ["10.0.0.1"],
            )
        assert result[0]["reachable"] is False

    def test_k8s_collects_cni(self):
        """K8s：采集 CNI 配置"""
        with patch("galaxy_diag.diagnoser.tools._collect_iptables", return_value=""), \
             patch("galaxy_diag.diagnoser.tools._collect_cni_config", return_value="CNI config"), \
             patch("galaxy_diag.diagnoser.tools._collect_docker_network", return_value=""):
            result = tools.collect_network_connectivity(
                EnvironmentType.CONTAINER, ContainerRuntime.KUBERNETES, [],
            )
        cni_results = [r for r in result if r["target"] == "CNI"]
        assert len(cni_results) == 1
        assert cni_results[0]["detail"] == "CNI config"

    def test_docker_collects_network(self):
        """Docker：采集 docker network"""
        with patch("galaxy_diag.diagnoser.tools._collect_iptables", return_value=""), \
             patch("galaxy_diag.diagnoser.tools._collect_cni_config", return_value=""), \
             patch("galaxy_diag.diagnoser.tools._collect_docker_network", return_value="docker nets"):
            result = tools.collect_network_connectivity(
                EnvironmentType.CONTAINER, ContainerRuntime.DOCKER, [],
            )
        net_results = [r for r in result if r["target"] == "docker-network"]
        assert len(net_results) == 1


# ===== _filter_log_lines 辅助函数 =====


class TestFilterLogLines:
    """日志过滤辅助函数"""

    def test_ansi_stripped(self):
        """ANSI 色码去除"""
        content = "\x1b[31mERROR disk failed\x1b[0m\n"
        filtered, level = tools._filter_log_lines(content, {"error"})
        assert "\x1b" not in filtered
        assert "ERROR disk failed" in filtered
        assert level == "ERROR"

    def test_no_match_returns_empty(self):
        """无匹配行 → 空内容"""
        content = "INFO all good\nINFO healthy\n"
        filtered, level = tools._filter_log_lines(content, {"error"})
        assert filtered == ""
        assert level == "Info"
