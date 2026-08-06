"""硬件资源预检测试

mock /proc/meminfo、shutil.disk_usage、nvidia-smi 等底层采集方法，
验证预检判定逻辑（通过/不通过/无GPU）。
"""

from unittest.mock import patch, MagicMock

import pytest

from galaxy_diag.config.defaults import HardwareRequirement
from galaxy_diag.model.precheck import HardwarePrechecker, CheckItem, PrecheckResult


# ============================================================
# 辅助：构造一个所有底层采集都被 mock 的预检器
# ============================================================

def make_prechecker(
    cpu_cores=8,
    ram_gb=16.0,
    disk_gb=100.0,
    gpu_vram_gb=None,  # None = 无 GPU
    **hw_kwargs,
):
    """创建预检器并 mock 底层采集方法"""
    hw = HardwareRequirement(**hw_kwargs)
    p = HardwarePrechecker(hw)

    ram_kb = int(ram_gb * 1024 * 1024)
    disk_bytes = int(disk_gb * 1024 ** 3)

    p._get_available_ram_gb = lambda: ram_gb
    p._get_available_disk_gb = lambda: disk_gb

    # mock os.cpu_count
    p._mock_cpu = cpu_cores

    # mock GPU
    p._mock_gpu_vram = gpu_vram_gb

    return p


def run_precheck(
    cpu_cores=8,
    ram_gb=16.0,
    disk_gb=100.0,
    gpu_vram_gb=None,
    **hw_kwargs,
):
    """一站式：创建预检器 → mock → 执行检查"""
    hw = HardwareRequirement(**hw_kwargs)
    prechecker = HardwarePrechecker(hw)

    ram_kb = int(ram_gb * 1024 * 1024)
    disk_bytes = int(disk_gb * 1024 ** 3)

    with patch("galaxy_diag.model.precheck.os.cpu_count", return_value=cpu_cores), \
         patch.object(prechecker, "_get_available_ram_gb", return_value=ram_gb), \
         patch.object(prechecker, "_get_available_disk_gb", return_value=disk_gb), \
         patch.object(prechecker, "_get_gpu_vram_gb", return_value=gpu_vram_gb):
        return prechecker.check()


# ============================================================
# 预检通过
# ============================================================

class TestPrecheckPass:
    def test_all_resources_sufficient(self):
        """所有资源充足，通过"""
        result = run_precheck(cpu_cores=8, ram_gb=16.0, disk_gb=100.0)
        assert result.passed is True
        assert all(item.passed for item in result.items)

    def test_exactly_at_threshold(self):
        """刚好等于阈值，通过"""
        result = run_precheck(
            cpu_cores=4, ram_gb=8.0, disk_gb=10.0,
            min_cpu_cores=4, min_ram_gb=8.0, min_disk_gb=10.0,
        )
        assert result.passed is True

    def test_no_gpu_gpu_optional(self):
        """无 GPU + gpu_required=False，通过"""
        result = run_precheck(gpu_vram_gb=None, gpu_required=False)
        assert result.passed is True
        gpu_item = next(i for i in result.items if i.name == "GPU 显存")
        assert gpu_item.passed is True
        assert "CPU 模式" in gpu_item.note


# ============================================================
# 预检不通过
# ============================================================

class TestPrecheckFail:
    def test_cpu_insufficient(self):
        """CPU 核数不足"""
        result = run_precheck(cpu_cores=2)
        assert result.passed is False
        cpu_item = next(i for i in result.items if i.name == "CPU 核数")
        assert cpu_item.passed is False

    def test_ram_insufficient(self):
        """内存不足"""
        result = run_precheck(ram_gb=4.0)
        assert result.passed is False
        ram_item = next(i for i in result.items if i.name == "内存")
        assert ram_item.passed is False

    def test_disk_insufficient(self):
        """磁盘不足"""
        result = run_precheck(disk_gb=5.0)
        assert result.passed is False
        disk_item = next(i for i in result.items if i.name == "磁盘")
        assert disk_item.passed is False

    def test_gpu_required_but_absent(self):
        """要求 GPU 但无 GPU"""
        result = run_precheck(gpu_vram_gb=None, gpu_required=True)
        assert result.passed is False
        gpu_item = next(i for i in result.items if i.name == "GPU 显存")
        assert gpu_item.passed is False

    def test_gpu_present_but_vram_insufficient(self):
        """有 GPU 但显存不足"""
        result = run_precheck(gpu_vram_gb=2.0)
        assert result.passed is False
        gpu_item = next(i for i in result.items if i.name == "GPU 显存")
        assert gpu_item.passed is False

    def test_multiple_deficiencies(self):
        """多项资源不足"""
        result = run_precheck(cpu_cores=2, ram_gb=4.0, disk_gb=5.0)
        assert result.passed is False
        failed = [i.name for i in result.items if not i.passed]
        assert len(failed) >= 3


# ============================================================
# CheckItem 数据结构
# ============================================================

class TestCheckItem:
    def test_fields(self):
        item = CheckItem(name="CPU 核数", required=4, actual=8, unit="核", passed=True)
        assert item.name == "CPU 核数"
        assert item.required == 4
        assert item.actual == 8
        assert item.unit == "核"
        assert item.passed is True
        assert item.note == ""

    def test_note_field(self):
        item = CheckItem(name="GPU 显存", required=6, actual=0, unit="GB", passed=True, note="CPU 模式")
        assert item.note == "CPU 模式"


# ============================================================
# PrecheckResult 汇总
# ============================================================

class TestPrecheckResult:
    def test_pass_summary(self):
        result = run_precheck(cpu_cores=8, ram_gb=16.0, disk_gb=100.0)
        assert "通过" in result.summary

    def test_fail_summary_contains_upgrade_hint(self):
        result = run_precheck(cpu_cores=2, ram_gb=4.0, disk_gb=5.0)
        assert "升级" in result.summary or "参考" in result.summary

    def test_fail_summary_lists_minimum_config(self):
        result = run_precheck(cpu_cores=2, ram_gb=4.0, disk_gb=5.0)
        assert "4" in result.summary  # CPU 最低 4 核
        assert "8" in result.summary or "8.0" in result.summary  # 内存最低 8 GB


# ============================================================
# 底层采集方法（验证 mock 行为正确）
# ============================================================

class TestLowLevelCollection:
    def test_get_available_disk_gb(self):
        """shutil.disk_usage 正确转换"""
        prechecker = HardwarePrechecker(HardwareRequirement())
        with patch("galaxy_diag.model.precheck.shutil.disk_usage") as mock_du:
            mock_du.return_value = MagicMock(free=100 * 1024 ** 3)  # 100 GB
            assert prechecker._get_available_disk_gb() == pytest.approx(100.0, abs=0.1)

    def test_get_gpu_vram_gb_no_nvidia(self):
        """nvidia-smi 不存在返回 None"""
        prechecker = HardwarePrechecker(HardwareRequirement())
        with patch("galaxy_diag.model.precheck.subprocess.run", side_effect=FileNotFoundError):
            assert prechecker._get_gpu_vram_gb() is None

    def test_get_gpu_vram_gb_success(self):
        """nvidia-smi 返回显存值"""
        prechecker = HardwarePrechecker(HardwareRequirement())
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "8192\n"  # 8192 MB = 8 GB
        with patch("galaxy_diag.model.precheck.subprocess.run", return_value=mock_result):
            assert prechecker._get_gpu_vram_gb() == pytest.approx(8.0, abs=0.1)
