import contextlib
import io
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from vpsbackup import recovery
from vpsbackup.cli import snapshot_table
from vpsbackup.common import BackupError


class RecoveryTests(unittest.TestCase):
    def fixture(self, name="opt/dockers/app/file.txt"):
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w") as tar:
            info = tarfile.TarInfo(name)
            info.size = 5
            tar.addfile(info, io.BytesIO(b"saved"))
        data.seek(0)
        return data

    def test_tar_normalizes_old_staging_path_and_preserves_bytes(self):
        source = self.fixture("var/lib/vps-backup-restic/work/stopped/app/file.txt")
        output = io.BytesIO()
        recovery.convert_tar(source, output, {"paths": ["/var/lib/vps-backup-restic/work/stopped/app"]}, "app")
        output.seek(0)
        with tarfile.open(fileobj=output) as tar:
            self.assertEqual(tar.extractfile("app/file.txt").read(), b"saved")

    def test_reject_unsafe_tar_members(self):
        for name in ("../outside", "/opt/dockers/app/file"):
            with self.assertRaises(BackupError):
                recovery.convert_tar(self.fixture(name), io.BytesIO(), {"paths": ["/opt/dockers/app"]}, "app")

    def test_latest_requires_app_for_multiple_apps(self):
        snapshots = [{"id": "a" * 64, "time": "2026-01-01", "tags": ["app:one"]},
                     {"id": "b" * 64, "time": "2026-01-02", "tags": ["app:two"]}]
        with self.assertRaises(BackupError):
            recovery.select(snapshots)
        self.assertEqual(recovery.select(snapshots, app="one")["id"], "a" * 64)

    def test_failed_export_leaves_no_partial_archive_and_keeps_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target = root / "app.tar.gz"
            config = {"state_dir": str(root / "state"), "reserve_mib": 64}
            saved = {"time": "2026-01-01", "id": "a" * 64, "tags": ["app:app"], "paths": ["/opt/dockers/app"]}
            @contextlib.contextmanager
            def broken(*args):
                yield self.fixture()
                raise BackupError("connection lost")
            with patch.object(recovery, "snapshots", return_value=[saved]), patch.object(recovery, "dump_stream", broken):
                with self.assertRaises(BackupError):
                    recovery.export(config, target=target, snapshot="a" * 8)
                self.assertEqual(list(root.iterdir()), [])
                target.write_text("existing")
                with self.assertRaises(BackupError):
                    recovery.export(config, target=target, snapshot="a" * 8)
                self.assertEqual(target.read_text(), "existing")

    def test_snapshot_table_is_readable(self):
        output = snapshot_table([{"id": "a" * 64, "time": "2026-11-16T21:00:00+03:00",
                                  "tags": ["app:melodix"], "hostname": "my-vps"}])
        self.assertIn("melodix", output)
        self.assertIn("aaaaaaaa", output)
        self.assertNotIn('"tree"', output)

    def test_stream_disk_failure_stops_and_reaps_child(self):
        command = [sys.executable, "-c", "import time; time.sleep(20)"]
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(recovery.client, "restic_command", return_value=(command, os.environ.copy())), \
                    patch.object(recovery, "check_space", side_effect=BackupError("disk reserve reached")):
                with self.assertRaisesRegex(BackupError, "disk reserve"):
                    with recovery.dump_stream({}, "a" * 64, None, Path(directory)) as source:
                        source.read()
                        raise tarfile.ReadError("incomplete archive")
