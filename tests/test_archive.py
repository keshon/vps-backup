import copy
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from vpsbackup import archive
from vpsbackup.common import BackupError


class ArchiveTests(unittest.TestCase):
    def test_fingerprint_ignores_timestamps_but_detects_changed_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "app"
            source.mkdir()
            path = source / "file"
            path.write_bytes(b"first")
            args = ({"id": "app"}, source, root / "sqlite", root / "app.tar.gz", root, 0)
            first = archive.build(*args)
            os.utime(path, (1, 1))
            self.assertEqual(first, archive.build(*args))
            path.write_bytes(b"other")
            self.assertNotEqual(first, archive.build(*args))

    def test_manifest_rejects_wrong_identity_tags_size_and_checksum(self):
        saved = {"format": "vps-backup-archive-v1", "id": "a" * 64,
                 "time": "2026-11-17T12:00:00+00:00", "app": "app", "tags": ["app:app"],
                 "archive": "app-test.tar.gz", "size": 100,
                 "sha256": "b" * 64, "fingerprint": "c" * 64}
        archive.validate_manifest(saved, saved["id"] + ".json")
        for field, value in (("id", "d" * 64), ("tags", ["app:other"]),
                             ("size", -1), ("sha256", "invalid"), ("time", "yesterday")):
            invalid = copy.deepcopy(saved)
            invalid[field] = value
            with self.subTest(field=field), self.assertRaises(BackupError):
                archive.validate_manifest(invalid, saved["id"] + ".json")

    def test_download_rejects_corruption_and_closes_response(self):
        response = io.BytesIO(b"corrupted")
        with patch.object(archive, "request", return_value=response):
            with self.assertRaisesRegex(BackupError, "checksum"):
                archive.verify({}, "data/app.tar.gz", "a" * 64)
        self.assertTrue(response.closed)
