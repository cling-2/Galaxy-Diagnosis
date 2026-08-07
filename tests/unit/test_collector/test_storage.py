"""存储采集测试（REQ-B-02）

覆盖 NAS / SAN / local 判定与工具缺失降级。
"""

from unittest.mock import patch

import pytest

from galaxy_diag.collector.storage import StorageCollector
from galaxy_diag.shared.types import EnvironmentType, StorageInfo


class TestNasCollection:
    def test_nfs_nas(self):
        findmnt_output = "/mnt/data nfs4 192.168.1.100:/export/data\n"
        collector = StorageCollector()
        with patch("galaxy_diag.collector.storage._run_cmd", return_value=findmnt_output), \
             patch("galaxy_diag.collector.storage._cmd_exists", return_value=True):
            results = collector._collect_nas([])
        assert len(results) == 1
        assert results[0].storage_type == "NAS"
        assert results[0].filesystem == "nfs4"
        assert results[0].mount_path == "/mnt/data"
        assert results[0].details.get("server") == "192.168.1.100"

    def test_cifs_nas(self):
        findmnt_output = "/mnt/share cifs //server/share\n"
        collector = StorageCollector()
        with patch("galaxy_diag.collector.storage._run_cmd", return_value=findmnt_output), \
             patch("galaxy_diag.collector.storage._cmd_exists", return_value=True):
            results = collector._collect_nas([])
        assert len(results) == 1
        assert results[0].storage_type == "NAS"
        assert results[0].filesystem == "cifs"

    def test_no_nas_mounts(self):
        """findmnt 存在但无 NAS 挂载（返回 None）→ 不警告，空列表"""
        collector = StorageCollector()
        with patch("galaxy_diag.collector.storage._run_cmd", return_value=None), \
             patch("galaxy_diag.collector.storage._cmd_exists", return_value=True):
            warnings = []
            results = collector._collect_nas(warnings)
        assert results == []
        assert not any("NAS" in w for w in warnings)

    def test_findmnt_missing(self):
        """findmnt 不存在 → 警告"""
        collector = StorageCollector()
        with patch("galaxy_diag.collector.storage._run_cmd", return_value=None), \
             patch("galaxy_diag.collector.storage._cmd_exists", return_value=False):
            warnings = []
            results = collector._collect_nas(warnings)
        assert results == []
        assert any("findmnt" in w for w in warnings)


class TestSanCollection:
    def test_iscsi_session(self):
        iscsi_output = (
            "tcp: [1] 192.168.1.50:3260,1 iqn.2026-01.com.example:storage.disk01\n"
        )
        collector = StorageCollector()

        def fake_run(args, timeout=5):
            if args[0] == "iscsiadm":
                return iscsi_output
            return None  # multipath 不可用

        with patch("galaxy_diag.collector.storage._run_cmd", side_effect=fake_run):
            results = collector._collect_san([])
        assert len(results) == 1
        assert results[0].storage_type == "SAN"
        assert results[0].filesystem == "iscsi"
        assert "iqn.2026" in results[0].details.get("target", "")

    def test_multipath(self):
        def fake_run(args, timeout=5):
            if args[0] == "iscsiadm":
                return None
            if args[0] == "multipath":
                return "mpatha (3600508...) dm-0\n"
            return None

        collector = StorageCollector()
        with patch("galaxy_diag.collector.storage._run_cmd", side_effect=fake_run):
            results = collector._collect_san([])
        assert len(results) == 1
        assert results[0].storage_type == "SAN"
        assert results[0].filesystem == "multipath"

    def test_no_san(self):
        """iscsiadm 与 multipath 均不可用 → 空列表，不警告"""
        collector = StorageCollector()
        with patch("galaxy_diag.collector.storage._run_cmd", return_value=None):
            warnings = []
            results = collector._collect_san(warnings)
        assert results == []
        # SAN 工具缺失不警告
        assert not any("SAN" in w or "iscsi" in w for w in warnings)


class TestLocalCollection:
    def test_local_filesystems(self):
        findmnt_output = (
            "/ ext4\n"
            "/boot xfs\n"
            "/run tmpfs\n"  # 伪文件系统，过滤
            "/sys sysfs\n"  # 伪文件系统，过滤
            "/data ext4\n"
        )
        collector = StorageCollector()
        with patch("galaxy_diag.collector.storage._run_cmd", return_value=findmnt_output), \
             patch("galaxy_diag.collector.storage._cmd_exists", return_value=True):
            results = collector._collect_local([])
        assert len(results) == 3  # / /boot /data
        assert all(r.storage_type == "local" for r in results)
        fstypes = {r.filesystem for r in results}
        assert fstypes == {"ext4", "xfs"}

    def test_dedup(self):
        """重复挂载点去重"""
        findmnt_output = "/ ext4\n/ ext4\n"
        collector = StorageCollector()
        with patch("galaxy_diag.collector.storage._run_cmd", return_value=findmnt_output), \
             patch("galaxy_diag.collector.storage._cmd_exists", return_value=True):
            results = collector._collect_local([])
        assert len(results) == 1

    def test_findmnt_missing(self):
        collector = StorageCollector()
        with patch("galaxy_diag.collector.storage._run_cmd", return_value=None), \
             patch("galaxy_diag.collector.storage._cmd_exists", return_value=False):
            warnings = []
            results = collector._collect_local(warnings)
        assert results == []
        assert any("findmnt" in w for w in warnings)


class TestStorageCollectorOrchestration:
    def test_collect_combines_all(self):
        collector = StorageCollector()
        with patch.object(collector, "_collect_nas", return_value=[
            StorageInfo(storage_type="NAS", mount_path="/mnt/nas", filesystem="nfs4")
        ]), patch.object(collector, "_collect_san", return_value=[]), \
             patch.object(collector, "_collect_local", return_value=[
            StorageInfo(storage_type="local", mount_path="/", filesystem="ext4")
        ]):
            results = collector.collect(EnvironmentType.BARE_METAL, [])
        assert len(results) == 2
        types = {r.storage_type for r in results}
        assert types == {"NAS", "local"}
