"""硬件资源预检

启动前检测 CPU 核数、内存、磁盘、GPU 显存，
不满足最低要求时给出明确提示并阻断启动。

优先使用标准库（os / shutil / /proc）而非 psutil，
减少离线环境依赖。GPU 检测仅支持 NVIDIA（nvidia-smi）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field

from galaxy_diag.config.defaults import HardwareRequirement


@dataclass
class CheckItem:
    """单项检测结果"""
    name: str           # "CPU 核数" / "GPU 显存" / "内存" / "磁盘"
    required: float     # 最低要求
    actual: float       # 实际值
    unit: str           # "核" / "GB"
    passed: bool        # 是否满足
    note: str = ""      # 额外说明（如"未检测到 GPU，将以 CPU 模式运行"）


@dataclass
class PrecheckResult:
    """预检汇总"""
    passed: bool
    items: list[CheckItem] = field(default_factory=list)
    summary: str = ""


class HardwarePrechecker:
    """硬件资源预检器"""

    def __init__(self, req: HardwareRequirement, embed_model: str = "", base_url: str = ""):
        self.req = req
        self.embed_model = embed_model
        self.base_url = base_url

    def check(self) -> PrecheckResult:
        """执行硬件预检

        Returns:
            PrecheckResult，passed=False 表示未通过
        """
        items = [
            self._check_cpu(),
            self._check_ram(),
            self._check_disk(),
        ]
        # embedding 模型可用性体检（REQ-X-02）：仅当配置了 embed_model 时
        if self.embed_model:
            items.append(self._check_embed_model())
        gpu_item = self._check_gpu()
        items.append(gpu_item)

        # 判定：非 GPU 项全过 + GPU 项满足（无 GPU 且 gpu_required=False 视为通过）
        hard_fail = [
            i for i in items
            if not i.passed and i.name != "GPU 显存"
        ]
        gpu_fail = (not gpu_item.passed) and (
            self.req.gpu_required or gpu_item.actual > 0  # 有 GPU 但显存不足，或要求 GPU
        )

        passed = len(hard_fail) == 0 and not gpu_fail

        return PrecheckResult(
            passed=passed,
            items=items,
            summary=self._build_summary(items, passed),
        )

    def _check_cpu(self) -> CheckItem:
        """检测 CPU 核数"""
        actual = os.cpu_count() or 0
        passed = actual >= self.req.min_cpu_cores
        return CheckItem(
            name="CPU 核数",
            required=self.req.min_cpu_cores,
            actual=actual,
            unit="核",
            passed=passed,
        )

    def _check_ram(self) -> CheckItem:
        """检测可用内存（GB）"""
        actual_gb = self._get_available_ram_gb()
        passed = actual_gb >= self.req.min_ram_gb
        return CheckItem(
            name="内存",
            required=self.req.min_ram_gb,
            actual=actual_gb,
            unit="GB",
            passed=passed,
        )

    def _check_disk(self) -> CheckItem:
        """检测磁盘可用空间（GB）"""
        actual_gb = self._get_available_disk_gb()
        passed = actual_gb >= self.req.min_disk_gb
        return CheckItem(
            name="磁盘",
            required=self.req.min_disk_gb,
            actual=actual_gb,
            unit="GB",
            passed=passed,
        )

    def _check_gpu(self) -> CheckItem:
        """检测 GPU 显存（GB）

        GPU 可选：有则检查显存，无则仅提示不阻断（gpu_required=False 时）。
        """
        vram_gb = self._get_gpu_vram_gb()

        if vram_gb is None:
            # 无 GPU
            if self.req.gpu_required:
                return CheckItem(
                    name="GPU 显存",
                    required=self.req.min_gpu_vram_gb,
                    actual=0,
                    unit="GB",
                    passed=False,
                    note="未检测到 GPU，但配置要求必须有 GPU",
                )
            return CheckItem(
                name="GPU 显存",
                required=self.req.min_gpu_vram_gb,
                actual=0,
                unit="GB",
                passed=True,  # gpu_required=False 时不阻断
                note="未检测到 GPU，将以 CPU 模式运行（推理速度较慢）",
            )

        passed = vram_gb >= self.req.min_gpu_vram_gb
        return CheckItem(
            name="GPU 显存",
            required=self.req.min_gpu_vram_gb,
            actual=vram_gb,
            unit="GB",
            passed=passed,
        )

    # ---------- 底层采集方法 ----------

    def _check_embed_model(self) -> CheckItem:
        """检测 embedding 模型可用性（通过 Ollama /api/embeddings）

        命中即视为通过；连不上 Ollama 或模型不存在视为未通过。
        """
        import json as _json
        import urllib.request
        passed = False
        note = ""
        # Ollama base_url 形如 http://host:port/v1，原生 API 在 /api/embeddings
        api_url = self.base_url.rstrip("/").removesuffix("/v1") + "/api/embeddings"
        try:
            payload = _json.dumps({"model": self.embed_model, "prompt": "ping"}).encode("utf-8")
            req = urllib.request.Request(
                api_url, data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    passed = True
                    note = f"embedding 模型 {self.embed_model} 可用"
        except Exception as e:
            note = f"embedding 模型 {self.embed_model} 不可用: {e}（请 ollama pull {self.embed_model}）"
        return CheckItem(
            name="embedding 模型",
            required=1,
            actual=1 if passed else 0,
            unit="",
            passed=passed,
            note=note,
        )

    def _get_available_ram_gb(self) -> float:
        """从 /proc/meminfo 读取可用内存（GB）"""
        try:
            with open("/proc/meminfo", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        # MemAvailable:  12345678 kB
                        kb = int(line.split()[1])
                        return kb / 1024 / 1024  # kB -> GB
        except (OSError, ValueError, IndexError):
            pass

        # 回退：非 Linux 或读取失败，用 shutil 估算（不精确）
        # 注意：shutil 没有直接获取内存的接口，此处返回 0 表示无法检测
        # 实际部署环境为 Linux，/proc/meminfo 可用
        return 0.0

    def _get_available_disk_gb(self) -> float:
        """获取根分区可用磁盘空间（GB）"""
        try:
            usage = shutil.disk_usage("/")
            return usage.free / 1024 / 1024 / 1024  # bytes -> GB
        except OSError:
            return 0.0

    def _get_gpu_vram_gb(self) -> float | None:
        """通过 nvidia-smi 获取 GPU 显存（GB）

        Returns:
            显存 GB 数；无 NVIDIA GPU 或查询失败返回 None
        """
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=memory.total",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return None
            # 取第一块 GPU 的显存
            first_line = result.stdout.strip().splitlines()[0]
            vram_mb = int(first_line)
            return vram_mb / 1024  # MB -> GB
        except (FileNotFoundError, subprocess.TimeoutExpired,
                ValueError, IndexError):
            return None

    def _build_summary(self, items: list[CheckItem], passed: bool) -> str:
        """构建人类可读的汇总"""
        lines = []
        header = "✅ 硬件资源预检通过" if passed else "❌ 硬件资源预检未通过"
        lines.append(header)
        lines.append("")

        for item in items:
            status = "✓" if item.passed else "✗"
            diff = ""
            if not item.passed and item.actual > 0:
                diff = f" (差 {item.required - item.actual:.1f} {item.unit})"
            note = f"  [{item.note}]" if item.note else ""
            lines.append(
                f"  {item.name}:   "
                f"需要 {item.required} {item.unit}, "
                f"实际 {item.actual:.1f} {item.unit}  {status}{diff}{note}"
            )

        if not passed:
            lines.append("")
            lines.append("  请升级硬件后重试。参考最低配置：")
            lines.append(f"  - CPU: {self.req.min_cpu_cores} 核及以上")
            lines.append(f"  - 内存: {self.req.min_ram_gb} GB 及以上")
            lines.append(f"  - 磁盘: {self.req.min_disk_gb} GB 及以上")
            lines.append(
                f"  - GPU: {self.req.min_gpu_vram_gb} GB 显存及以上"
                f"（可选，无 GPU 将以 CPU 模式运行）"
            )

        return "\n".join(lines)
