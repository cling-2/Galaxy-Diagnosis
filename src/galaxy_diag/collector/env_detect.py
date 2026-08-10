"""环境类型识别模块（REQ-B-01）

通过系统特征组合检测运行环境类型（裸金属 / 虚拟机 / 容器），
优先级：CONTAINER > VM > BARE_METAL。

对齐 Environment_awareness_design.md §环境识别设计。
"""

from __future__ import annotations

import os
import subprocess
from typing import Protocol

from galaxy_diag.shared.types import ContainerRuntime, EnvironmentType


# ===== 模块级工具函数（便于按模块路径 patch 测试） =====


def _read_file(path: str) -> str | None:
    """读取文件全部内容，失败返回 None"""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _run_cmd(args: list[str], timeout: int = 5) -> str | None:
    """执行命令并返回 stdout，失败返回 None（不抛异常）

    所有 subprocess 调用的唯一出口，便于统一 patch。
    """
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


# ===== VM 厂商关键词 =====

_VM_KEYWORDS = frozenset({
    "vmware", "kvm", "qemu", "xen", "virtualbox",
    "microsoft", "oracle", "parallels", "bhyve",
})

_CONTAINER_CGROUP_KEYWORDS = frozenset({
    "docker", "containerd", "kubepods",
})


# ===== Detector 协议 =====


class _Detector(Protocol):
    """环境检测策略协议"""

    def detect(self, warnings: list[str]) -> EnvironmentType | None:
        ...


# ===== ContainerDetector =====


class ContainerDetector:
    """容器环境检测

    信号：
    1. /.dockerenv 文件存在
    2. /proc/1/cgroup 含 docker/containerd/kubepods
    3. /proc/self/mountinfo 含容器运行时 overlay 挂载
    """

    def detect(self, warnings: list[str]) -> EnvironmentType | None:
        # 信号 1: /.dockerenv
        if os.path.exists("/.dockerenv"):
            return EnvironmentType.CONTAINER

        # 信号 2: /proc/1/cgroup
        cgroup = _read_file("/proc/1/cgroup")
        if cgroup:
            lower = cgroup.lower()
            if any(kw in lower for kw in _CONTAINER_CGROUP_KEYWORDS):
                return EnvironmentType.CONTAINER

        # 信号 3: /proc/self/mountinfo 含 overlay 挂载在根
        mountinfo = _read_file("/proc/self/mountinfo")
        if mountinfo:
            for line in mountinfo.splitlines():
                # mountinfo 格式: "ID Parent Major:Min Root MountPoint Options - FSTYPE Source ..."
                # parts[4] = MountPoint，"overlay" 出现在 " - " 之后的 FSTYPE/Source 部分
                parts = line.split()
                if len(parts) >= 10 and parts[4] == "/" and "overlay" in parts[6:]:
                    return EnvironmentType.CONTAINER

        return None


# ===== VMDetector =====


class VMDetector:
    """虚拟机环境检测

    信号（按优先级）：
    1. systemd-detect-virt 输出含虚拟化类型
    2. /sys/class/dmi/id/product_name 含 VM 厂商特征
    3. /sys/class/scsi_disk/*/device/vendor 含 VM 厂商
    """

    def detect(self, warnings: list[str]) -> EnvironmentType | None:
        # 信号 1: systemd-detect-virt
        virt = _run_cmd(["systemd-detect-virt"])
        if virt is not None:
            lower = virt.lower()
            if lower == "none":
                pass  # 明确非 VM，继续下一信号
            elif any(kw in lower for kw in _VM_KEYWORDS):
                return EnvironmentType.VM
            # systemd-detect-virt 可运行但返回未知类型（如 wsl），
            # 不视为 VM，继续下一信号

        # 信号 2: DMI 产品名
        product = _read_file("/sys/class/dmi/id/product_name")
        if product:
            lower = product.lower()
            if any(kw in lower for kw in _VM_KEYWORDS):
                return EnvironmentType.VM

        # 信号 3: SCSI 厂商
        try:
            scsi_dir = "/sys/class/scsi_disk"
            if os.path.isdir(scsi_dir):
                for entry in os.listdir(scsi_dir):
                    vendor_path = os.path.join(scsi_dir, entry, "device", "vendor")
                    vendor = _read_file(vendor_path)
                    if vendor:
                        lower = vendor.lower()
                        if any(kw in lower for kw in _VM_KEYWORDS):
                            return EnvironmentType.VM
        except PermissionError:
            warnings.append("无权限读取 SCSI 设备信息，VM 信号可能遗漏")

        return None


# ===== BareMetalDetector =====


class BareMetalDetector:
    """裸金属环境（兜底）"""

    def detect(self, warnings: list[str]) -> EnvironmentType:
        return EnvironmentType.BARE_METAL


# ===== EnvironmentDetector 顶层 =====


class EnvironmentDetector:
    """环境类型识别器

    按优先级链执行：Container > VM > BareMetal
    命中即返回，嵌套环境以容器优先。
    """

    def __init__(self) -> None:
        self._detectors: list[_Detector] = [
            ContainerDetector(),
            VMDetector(),
            BareMetalDetector(),
        ]

    def detect(self, warnings: list[str] | None = None) -> EnvironmentType:
        """识别当前运行环境类型

        Args:
            warnings: 采集警告列表（就地追加），为 None 时自动创建。

        Returns:
            EnvironmentType 枚举值
        """
        if warnings is None:
            warnings = []

        for detector in self._detectors:
            result = detector.detect(warnings)
            if result is not None:
                return result

        # 理论上不会到达（BareMetalDetector 总返回非 None），兜底
        warnings.append("所有环境检测信号均未命中，默认为裸金属")
        return EnvironmentType.BARE_METAL


# ===== 容器运行时子类型识别 =====


def detect_container_runtime(warnings: list[str] | None = None) -> ContainerRuntime:
    """识别容器运行时子类型（Docker / Kubernetes / Unknown）

    仅当 env_type == CONTAINER 时调用。优先级 K8S > DOCKER：
    K8s Pod 底层可能用 docker/containerd 运行时，若先判 Docker 会误判。

    信号：
    1. /var/run/secrets/kubernetes.io/serviceaccount/token 存在 → KUBERNETES
    2. KUBERNETES_SERVICE_HOST 环境变量存在 → KUBERNETES
    3. /proc/1/cgroup 含 kubepods → KUBERNETES
    4. /.dockerenv 存在或 /proc/1/cgroup 含 docker → DOCKER
    5. 兜底 → UNKNOWN

    Args:
        warnings: 采集警告列表（就地追加），为 None 时自动创建。

    Returns:
        ContainerRuntime 枚举值
    """
    if warnings is None:
        warnings = []

    # --- Kubernetes 信号（优先） ---

    # 信号 1: K8s service account 挂载（Pod 内自动注入）
    if os.path.exists("/var/run/secrets/kubernetes.io/serviceaccount/token"):
        return ContainerRuntime.KUBERNETES

    # 信号 2: KUBERNETES_SERVICE_HOST 环境变量（K8s Downward API 注入）
    k8s_host = os.environ.get("KUBERNETES_SERVICE_HOST", "").strip()
    if k8s_host:
        return ContainerRuntime.KUBERNETES

    # 信号 3: /proc/1/cgroup 含 kubepods
    cgroup = _read_file("/proc/1/cgroup")
    if cgroup and "kubepods" in cgroup.lower():
        return ContainerRuntime.KUBERNETES

    # --- Docker 信号 ---

    # 信号 4: /.dockerenv 存在
    if os.path.exists("/.dockerenv"):
        return ContainerRuntime.DOCKER

    # 信号 5: /proc/1/cgroup 含 docker（不含 kubepods，否则已在上面命中）
    if cgroup and "docker" in cgroup.lower():
        return ContainerRuntime.DOCKER

    # --- 兜底 ---
    warnings.append(
        "容器运行时子类型未确定（非 Docker 亦非 Kubernetes），"
        "后续采集将尝试双路命令（Docker + K8s）"
    )
    return ContainerRuntime.UNKNOWN
