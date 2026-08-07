"""硬件信息采集模块（REQ-B-02）

采集 CPU / 内存 / 磁盘 / RAID 卡 / 网卡信息，全部只读操作。
各子采集器独立降级：单项失败不阻断整体，记入 warnings。

对齐 Environment_awareness_design.md §信息采集设计。
"""

from __future__ import annotations

import os
import re
import subprocess

from galaxy_diag.shared.types import (
    DiskInfo,
    EnvironmentType,
    HardwareInfo,
    NicInfo,
    RaidCardInfo,
)


# ===== 模块级工具函数 =====


def _read_file(path: str) -> str | None:
    """读取文件全部内容，失败返回 None"""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _run_cmd(args: list[str], timeout: int = 5) -> str | None:
    """执行命令返回 stdout，失败返回 None（不抛异常）"""
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


def _bytes_to_human(num_bytes: int | float) -> str:
    """字节数转人类可读容量字符串"""
    try:
        size = float(num_bytes)
    except (TypeError, ValueError):
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(size) < 1024.0:
            if unit in ("B", "KB", "MB"):
                return f"{size:.0f}{unit}"
            return f"{size:.1f}{unit}"
        size /= 1024.0
    return f"{size:.1f}PB"


# ===== HardwareCollector =====


class HardwareCollector:
    """硬件信息采集器

    根据 env_type 差异化采集：裸金属采全量，容器跳过 RAID 等不可见项。
    """

    def __init__(self) -> None:
        # 原始输出（供 LLM 上下文，截断后使用）
        self.raw_output: dict[str, str] = {}

    def collect(
        self,
        env_type: EnvironmentType,
        warnings: list[str],
    ) -> HardwareInfo:
        """采集硬件信息

        Args:
            env_type: 环境类型（影响采集策略）
            warnings: 采集警告列表（就地追加）

        Returns:
            HardwareInfo
        """
        cpu_model, cpu_cores = self._collect_cpu()
        memory = self._collect_memory()
        disks = self._collect_disks(warnings)
        nics = self._collect_nics(warnings)

        # RAID 采集：容器环境跳过（不可见，避免无效尝试污染 warnings）
        raid_cards: list[RaidCardInfo] = []
        if env_type == EnvironmentType.CONTAINER:
            warnings.append(
                "容器环境无法采集宿主机 RAID 卡信息，建议在宿主机上执行 galaxy-diag env 补充"
            )
        else:
            raid_cards = self._collect_raid(warnings)

        return HardwareInfo(
            cpu_model=cpu_model,
            cpu_cores=cpu_cores,
            memory_total_gb=memory,
            disks=disks,
            raid_cards=raid_cards,
            nics=nics,
        )

    # ===== 子采集器 =====

    def _collect_cpu(self) -> tuple[str, int]:
        """采集 CPU 型号与核数

        Returns:
            (cpu_model, cpu_cores)
        """
        content = _read_file("/proc/cpuinfo")
        if content is None:
            return "", 0

        self.raw_output["cpuinfo"] = content

        model = ""
        cores = 0
        for line in content.splitlines():
            if line.startswith("model name") and ":" in line:
                model = line.split(":", 1)[1].strip()
            elif line.startswith("processor") and ":" in line:
                cores += 1

        # 兜底：通过 os.cpu_count() 估算
        if cores == 0:
            cores = os.cpu_count() or 0

        return model, cores

    def _collect_memory(self) -> float:
        """采集内存总容量（GB）"""
        content = _read_file("/proc/meminfo")
        if content is None:
            return 0.0

        self.raw_output["meminfo"] = content

        for line in content.splitlines():
            if line.startswith("MemTotal:"):
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        kb = int(parts[1])
                        return round(kb / 1024 / 1024, 1)
                    except ValueError:
                        return 0.0
        return 0.0

    def _collect_disks(self, warnings: list[str]) -> list[DiskInfo]:
        """采集磁盘信息（lsblk）"""
        output = _run_cmd(["lsblk", "-b", "-P", "-o", "NAME,TYPE,SIZE,MODEL"])
        if output is None:
            warnings.append("lsblk 不可用，磁盘信息未采集")
            return []

        self.raw_output["lsblk"] = output

        disks: list[DiskInfo] = []
        for line in output.splitlines():
            parsed = _parse_lsblk_line(line)
            if not parsed:
                continue
            # 仅采集整盘（disk/lvm/raid 等），跳过分区/回环/光驱
            if parsed["TYPE"] in ("part", "rom", "loop"):
                continue
            if parsed["TYPE"] != "disk":
                continue

            try:
                size_bytes = int(parsed["SIZE"])
            except (ValueError, KeyError):
                size_bytes = 0

            disks.append(DiskInfo(
                type=_infer_disk_type(parsed.get("NAME", "")),
                capacity=_bytes_to_human(size_bytes),
                model=parsed.get("MODEL", "").strip() or parsed.get("NAME", ""),
            ))

        return disks

    def _collect_raid(self, warnings: list[str]) -> list[RaidCardInfo]:
        """采集 RAID 卡信息（storcli64 / megacli / lspci）"""
        # 优先 storcli64
        cards = self._collect_raid_storcli()
        if cards is not None:
            return cards

        # 次选 megacli
        cards = self._collect_raid_megacli()
        if cards is not None:
            return cards

        # 兜底 lspci
        cards = self._collect_raid_lspci()
        if cards is not None:
            return cards

        warnings.append("RAID 采集工具（storcli64/megacli/lspci）均不可用，RAID 卡信息未采集")
        return []

    def _collect_raid_storcli(self) -> list[RaidCardInfo] | None:
        output = _run_cmd(["storcli64", "/c0", "show"])
        if output is None:
            return None
        self.raw_output["raid_storcli"] = output

        cards: list[RaidCardInfo] = []
        model = ""
        firmware = ""
        for line in output.splitlines():
            lower = line.lower()
            # storcli 输出格式多样：Product Name / Model / Controller ...
            if "=" in line:
                key, _, value = line.partition("=")
                key_lower = key.strip().lower()
                value = value.strip()
                if any(k in key_lower for k in ("product name", "model", "controller")) and value:
                    if not model:  # 取第一个匹配
                        model = value
                elif "firmware" in key_lower and "version" in key_lower and value:
                    if not firmware:
                        firmware = value
        if model:
            cards.append(RaidCardInfo(model=model, firmware_version=firmware))
        return cards

    def _collect_raid_megacli(self) -> list[RaidCardInfo] | None:
        output = _run_cmd(["megacli", "-AdpAllInfo", "-aALL"])
        if output is None:
            return None
        self.raw_output["raid_megacli"] = output

        cards: list[RaidCardInfo] = []
        model = ""
        firmware = ""
        for line in output.splitlines():
            lower = line.lower()
            if "product name" in lower and ":" in line:
                model = line.split(":", 1)[1].strip()
            elif "fw version" in lower and ":" in line:
                firmware = line.split(":", 1)[1].strip()
        if model:
            cards.append(RaidCardInfo(model=model, firmware_version=firmware))
        return cards

    def _collect_raid_lspci(self) -> list[RaidCardInfo] | None:
        output = _run_cmd(["lspci"])
        if output is None:
            return None
        self.raw_output["lspci"] = output

        cards: list[RaidCardInfo] = []
        for line in output.splitlines():
            lower = line.lower()
            if "raid" in lower or "sata controller" in lower or "sas" in lower:
                # lspci 行格式: "00:1f.2 RAID bus controller: Intel ... (rev 03)"
                if ":" in line:
                    desc = line.split(":", 1)[1].strip()
                    # 去掉 (rev xx) 等后缀
                    desc = re.sub(r"\s*\(rev[^)]*\)\s*$", "", desc).strip()
                    cards.append(RaidCardInfo(model=desc, firmware_version=""))
        return cards

    def _collect_nics(self, warnings: list[str]) -> list[NicInfo]:
        """采集网卡信息（lspci + /sys/class/net 驱动）"""
        nics: list[NicInfo] = []
        output = _run_cmd(["lspci"])

        if output is not None:
            self.raw_output.setdefault("lspci", output)
            for line in output.splitlines():
                lower = line.lower()
                if "ethernet controller" in lower or "network controller" in lower:
                    if ":" in line:
                        desc = line.split(":", 1)[1].strip()
                        desc = re.sub(r"\s*\(rev[^)]*\)\s*$", "", desc).strip()
                        driver = _lookup_nic_driver(desc)
                        nics.append(NicInfo(model=desc, driver=driver))
            if nics:
                return nics

        # 兜底：仅枚举网卡接口名（无型号）
        interfaces = self._enumerate_net_interfaces()
        if interfaces:
            warnings.append("lspci 不可用，仅采集网卡接口名，缺少网卡型号信息")
            for iface in interfaces:
                driver = _read_sysfs_driver(iface)
                nics.append(NicInfo(model=iface, driver=driver))
        else:
            warnings.append("网卡信息采集失败")

        return nics

    def _enumerate_net_interfaces(self) -> list[str]:
        """枚举 /sys/class/net 下的物理网卡（排除 lo）"""
        try:
            entries = os.listdir("/sys/class/net")
        except OSError:
            return []
        result = []
        for entry in entries:
            if entry == "lo":
                continue
            # 排除虚拟接口（veth*/docker*/br-*）
            if entry.startswith(("veth", "docker", "br-", "virbr")):
                continue
            result.append(entry)
        return result


# ===== 模块级辅助函数 =====


def _parse_lsblk_line(line: str) -> dict[str, str] | None:
    """解析 lsblk -P 输出的一行，如 NAME="sda" TYPE="disk" SIZE="..." MODEL="..."

    Returns:
        字段字典，解析失败返回 None
    """
    if not line.strip():
        return None
    parsed: dict[str, str] = {}
    for match in re.finditer(r'(\w+)="([^"]*)"', line):
        parsed[match.group(1)] = match.group(2)
    return parsed if parsed else None


def _infer_disk_type(name: str) -> str:
    """根据设备名推断磁盘类型（简化版）"""
    if name.startswith("nvme"):
        return "NVMe"
    if name.startswith("sd"):
        # 无法从名字判断 SSD/HDD，统一标记为磁盘
        return "disk"
    if name.startswith("vd") or name.startswith("xvd"):
        return "Virtual"
    return "disk"


def _read_sysfs_driver(iface: str) -> str:
    """读取 /sys/class/net/<iface>/device/driver 的驱动名"""
    try:
        link = os.readlink(f"/sys/class/net/{iface}/device/driver")
        return os.path.basename(link)
    except OSError:
        return ""


def _lookup_nic_driver(desc: str) -> str:
    """根据网卡描述查找对应驱动（通过 sysfs 网卡接口匹配）

    简化实现：遍历 /sys/class/net 找到第一个物理网卡的驱动。
    """
    try:
        entries = os.listdir("/sys/class/net")
    except OSError:
        return ""
    for entry in entries:
        if entry == "lo" or entry.startswith(("veth", "docker", "br-", "virbr")):
            continue
        driver = _read_sysfs_driver(entry)
        if driver:
            return driver
    return ""
