"""硬件资源预检接入 CLI 启动流程的集成测试 (REQ-A-01 验收标准 6)

覆盖：
- 预检通过 → 正常启动
- 预检失败 → sys.exit(1) 拒绝启动
- --skip-precheck → 跳过预检
- display.print_precheck_result 渲染
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

from galaxy_diag.model.precheck import CheckItem, PrecheckResult


class TestPrecheckIntegration:
    """预检在 CLI main() 中的集成行为"""

    def test_precheck_pass_allows_startup(self):
        """预检通过时不阻断启动"""
        from galaxy_diag.workflow.cli.app import _run_precheck

        passed_result = PrecheckResult(
            passed=True,
            items=[
                CheckItem(name="CPU 核数", required=4, actual=8, unit="核", passed=True),
                CheckItem(name="内存", required=8.0, actual=16.0, unit="GB", passed=True),
            ],
            summary="✅ 硬件资源预检通过",
        )

        with patch("galaxy_diag.workflow.cli.app.HardwarePrechecker") as MockPrechecker, \
             patch("galaxy_diag.workflow.cli.app.load_config") as mock_load:
            MockPrechecker.return_value.check.return_value = passed_result
            mock_load.return_value = MagicMock()
            # 不应 sys.exit
            _run_precheck(skip=False, config_path=None)

    def test_precheck_fail_refuses_startup(self):
        """预检失败时 sys.exit(1) 拒绝启动"""
        from galaxy_diag.workflow.cli.app import _run_precheck

        failed_result = PrecheckResult(
            passed=False,
            items=[
                CheckItem(name="CPU 核数", required=4, actual=2, unit="核", passed=False),
                CheckItem(name="内存", required=8.0, actual=4.0, unit="GB", passed=False),
            ],
            summary="❌ 硬件资源预检未通过\n  CPU 核数:   需要 4 核, 实际 2 核  ✗\n  内存:   需要 8.0 GB, 实际 4.0 GB  ✗",
        )

        with patch("galaxy_diag.workflow.cli.app.HardwarePrechecker") as MockPrechecker, \
             patch("galaxy_diag.workflow.cli.app.load_config") as mock_load:
            MockPrechecker.return_value.check.return_value = failed_result
            mock_load.return_value = MagicMock()
            with pytest.raises(SystemExit) as exc_info:
                _run_precheck(skip=False, config_path=None)
            assert exc_info.value.code == 1

    def test_skip_precheck_bypasses_check(self):
        """--skip-precheck 跳过预检，不调用 HardwarePrechecker"""
        from galaxy_diag.workflow.cli.app import _run_precheck

        with patch("galaxy_diag.workflow.cli.app.HardwarePrechecker") as MockPrechecker:
            _run_precheck(skip=True, config_path=None)
            # HardwarePrechecker 不应被实例化
            MockPrechecker.assert_not_called()

    def test_precheck_exception_does_not_block(self):
        """预检自身异常不阻断启动（降级警告）"""
        from galaxy_diag.workflow.cli.app import _run_precheck

        with patch("galaxy_diag.workflow.cli.app.load_config", side_effect=Exception("config broken")):
            # 不应 sys.exit，应静默降级
            _run_precheck(skip=False, config_path=None)


class TestPrecheckGating:
    """_needs_precheck 谓词：仅 run/diagnose（非 mock）触发预检"""

    def _ns(self, **kw) -> "argparse.Namespace":
        import argparse
        return argparse.Namespace(**kw)

    def test_run_needs_precheck(self):
        from galaxy_diag.workflow.cli.app import _needs_precheck
        assert _needs_precheck(self._ns(command="run")) is True

    def test_diagnose_needs_precheck(self):
        from galaxy_diag.workflow.cli.app import _needs_precheck
        assert _needs_precheck(self._ns(command="diagnose")) is True

    @pytest.mark.parametrize("cmd", ["env", "snapshot", "audit-log", "completion", "fix", "review"])
    def test_non_llm_commands_skip_precheck(self, cmd):
        from galaxy_diag.workflow.cli.app import _needs_precheck
        assert _needs_precheck(self._ns(command=cmd)) is False

    def test_mock_skips_precheck(self):
        from galaxy_diag.workflow.cli.app import _needs_precheck
        assert _needs_precheck(self._ns(command="run", mock=True)) is False

    def test_no_command_skips_precheck(self):
        from galaxy_diag.workflow.cli.app import _needs_precheck
        assert _needs_precheck(self._ns(command=None)) is False

    def test_skip_flag_orthogonal(self):
        """_needs_precheck 与 --skip-precheck 正交：skip 在 _run_precheck 内处理"""
        from galaxy_diag.workflow.cli.app import _needs_precheck
        # run 即使带 skip_precheck 仍返回 True（gating 层不关心 skip）
        assert _needs_precheck(self._ns(command="run", skip_precheck=True)) is True


class TestPrecheckGatingIntegration:
    """main() 分发时是否实际调用 HardwarePrechecker（端到端 gating 验证）"""

    def test_non_llm_command_does_not_trigger_precheck(self):
        """snapshot list 不调用 LLM，main() 不应触发 HardwarePrechecker"""
        with patch("galaxy_diag.workflow.cli.app.HardwarePrechecker") as MockPrechecker, \
             patch("galaxy_diag.safety.snapshot.list_snapshots", return_value=[]), \
             patch("sys.argv", ["galaxy-diag", "snapshot", "list"]):
            try:
                from galaxy_diag.workflow.cli.app import main
                main()
            except SystemExit:
                pass
            MockPrechecker.assert_not_called()

    def test_llm_command_triggers_precheck(self):
        """diagnose 调用 LLM，main() 应触发 HardwarePrechecker

        预检通过后进入 handle()；patch 掉 handle 避免真实采集/推理。
        set_defaults(callback=handle) 在 _build_parser() 注册时绑定函数对象，
        因此需在 main() 构建解析器前 patch 模块属性。
        """
        with patch("galaxy_diag.workflow.cli.cmd_diagnose.handle") as mock_handle, \
             patch("galaxy_diag.workflow.cli.app.HardwarePrechecker") as MockPrechecker, \
             patch("sys.argv", ["galaxy-diag", "diagnose", "-d", "x"]):
            MockPrechecker.return_value.check.return_value = PrecheckResult(
                passed=True,
                items=[CheckItem(name="CPU 核数", required=4, actual=8, unit="核", passed=True)],
                summary="✅ ok",
            )
            try:
                from galaxy_diag.workflow.cli.app import main
                main()
            except SystemExit:
                pass
            assert MockPrechecker.called is True
            assert mock_handle.called is True


class TestPrintPrecheckResult:
    """print_precheck_result 渲染测试"""

    def test_render_passed(self, capsys):
        """预检通过时的渲染"""
        from galaxy_diag.workflow.cli.display import init_console, print_precheck_result

        init_console(no_color=True)
        result = PrecheckResult(
            passed=True,
            items=[
                CheckItem(name="CPU 核数", required=4, actual=8, unit="核", passed=True),
                CheckItem(name="内存", required=8.0, actual=16.0, unit="GB", passed=True),
                CheckItem(name="磁盘", required=10.0, actual=100.0, unit="GB", passed=True),
                CheckItem(name="GPU 显存", required=6.0, actual=0, unit="GB", passed=True,
                          note="未检测到 GPU，将以 CPU 模式运行"),
            ],
            summary="✅ 硬件资源预检通过",
        )
        # 不应抛异常
        print_precheck_result(result)

    def test_render_failed(self, capsys):
        """预检失败时的渲染"""
        from galaxy_diag.workflow.cli.display import init_console, print_precheck_result

        init_console(no_color=True)
        result = PrecheckResult(
            passed=False,
            items=[
                CheckItem(name="CPU 核数", required=4, actual=2, unit="核", passed=False),
                CheckItem(name="内存", required=8.0, actual=4.0, unit="GB", passed=False),
            ],
            summary="❌ 硬件资源预检未通过\n  CPU 核数:   需要 4 核, 实际 2 核  ✗",
        )
        # 不应抛异常
        print_precheck_result(result)
