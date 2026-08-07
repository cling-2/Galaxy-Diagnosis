"""第三方存储信息采集模块（REQ-B-02）

采集 SAN / NAS / 本地存储信息，全部只读操作。
按文件系统/协议区分存储类型，工具缺失时降级并记录 warning。

对齐 Environment_awareness_design.md §信息采集设计 §存储类型判定逻辑。
"""

from __future__ import annotations

import subprocess

from galaxy_diag.shared.types import EnvironmentType, StorageInfo


# ===== 模块级工具函数 =====


def _run_cmd(args: list[str], timeout: int = 5) -> str | None:
    """执行命令返回 stdout，失败返回 None"""
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


# 伪文件系统过滤（不纳入存储信息）
_PSEUDO_FS = frozenset({
    "sysfs", "proc", "tmpfs", "devtmpfs", "devpts",
    "cgroup", "cgroup2", "pstore", "debugfs",
    "tracefs", "securityfs", "fusectl", "configfs",
    "efivarfs", "bpf", "mqueue", "hugetlbfs",
    "overlay", "squashfs",
})

# 本地文件系统类型
_LOCAL_FS = frozenset({"ext2", "ext3", "ext4", "xfs", "btrfs", "jfs", "reiserfs"})


# ===== StorageCollector =====


class StorageCollector:
    """第三方存储信息采集器"""

    def __init__(self) -> None:
        self.raw_output: dict[str, str] = {}

    def collect(
        self,
        env_type: EnvironmentType,
        warnings: list[str],
    ) -> list[StorageInfo]:
        """采集存储信息

        Args:
            env_type: 环境类型（影响采集策略）
            warnings: 采集警告列表（就地追加）

        Returns:
            StorageInfo 列表
        """
        results: list[StorageInfo] = []

        # NAS: NFS / CIFS 挂载
        nas_list = self._collect_nas(warnings)
        results.extend(nas_list)

        # SAN: iSCSI / FC 多路径
        san_list = self._collect_san(warnings)
        results.extend(san_list)

        # 本地存储
        local_list = self._collect_local(warnings)
        results.extend(local_list)

        return results

    # ===== NAS 采集 =====

    def _collect_nas(self, warnings: list[str]) -> list[StorageInfo]:
        """采集 NAS 存储（NFS / CIFS）"""
        output = _run_cmd(["findmnt", "-t", "nfs,nfs4,cifs", "-o", "TARGET,FSTYPE,SOURCE", "-n"])
        if output is None:
            # findmnt 缺失或无 NAS 挂载（无挂载时 findmnt 返回非 0，_run_cmd 返回 None）
            # 只有命令不存在才警告
            if _cmd_exists("findmnt"):
                # findmnt 存在但返回非 0 = 无 NAS 挂载，不警告
                pass
            else:
                warnings.append("findmnt 不可用，NAS 存储信息未采集")
            return []

        self.raw_output["findmnt_nas"] = output

        results: list[StorageInfo] = []
        for line in output.splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            target = parts[0]
            fstype = parts[1]
            source = parts[2] if len(parts) >= 3 else ""

            storage_type = "NAS"
            details: dict = {}
            if source:
                details["source"] = source
            # NFS: 提取 server 路径
            if ":" in source:
                server, export = source.split(":", 1)
                details["server"] = server
                details["export"] = export

            results.append(StorageInfo(
                storage_type=storage_type,  # type: ignore[arg-type]
                mount_path=target,
                filesystem=fstype,
                details=details,
            ))

        return results

    # ===== SAN 采集 =====

    def _collect_san(self, warnings: list[str]) -> list[StorageInfo]:
        """采集 SAN 存储（iSCSI / FC 多路径）"""
        results: list[StorageInfo] = []

        # iSCSI 会话
        iscsi_output = _run_cmd(["iscsiadm", "-m", "session"])
        if iscsi_output is not None:
            self.raw_output["iscsiadm_session"] = iscsi_output
            for line in iscsi_output.splitlines():
                # 格式: "tcp: [1] 192.168.1.1:3260,1 iqn.2026..."
                if not line.strip():
                    continue
                target_iqn = ""
                if "iqn." in line:
                    idx = line.index("iqn.")
                    target_iqn = line[idx:].strip()
                results.append(StorageInfo(
                    storage_type="SAN",  # type: ignore[arg-type]
                    mount_path="",
                    filesystem="iscsi",
                    details={"target": target_iqn} if target_iqn else {},
                ))
        # iscsiadm 不可用不警告（很多系统没有 iSCSI）

        # 多路径
        mp_output = _run_cmd(["multipath", "-ll"])
        if mp_output is not None and mp_output.strip():
            self.raw_output["multipath"] = mp_output
            # 有多路径设备则追加 SAN 条目
            results.append(StorageInfo(
                storage_type="SAN",  # type: ignore[arg-type]
                mount_path="",
                filesystem="multipath",
                details={"multipath": True},
            ))
        # multipath 不可用不警告（很多系统没有多路径）

        return results

    # ===== 本地存储采集 =====

    def _collect_local(self, warnings: list[str]) -> list[StorageInfo]:
        """采集本地存储（ext4/xfs/btrfs 等）"""
        output = _run_cmd(["findmnt", "-o", "TARGET,FSTYPE", "-n"])
        if output is None:
            if not _cmd_exists("findmnt"):
                warnings.append("findmnt 不可用，本地存储信息未采集")
            return []

        self.raw_output.setdefault("findmnt_local", output)

        results: list[StorageInfo] = []
        seen: set[str] = set()
        for line in output.splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            target = parts[0]
            fstype = parts[1]

            # 过滤伪文件系统
            if fstype in _PSEUDO_FS:
                continue
            # 仅采集已知本地文件系统
            if fstype not in _LOCAL_FS:
                continue
            # 去重
            if target in seen:
                continue
            seen.add(target)

            results.append(StorageInfo(
                storage_type="local",  # type: ignore[arg-type]
                mount_path=target,
                filesystem=fstype,
                details={},
            ))

        return results


# ===== 辅助函数 =====


def _cmd_exists(cmd: str) -> bool:
    """检查命令是否存在于 PATH"""
    import shutil
    return shutil.which(cmd) is not None
