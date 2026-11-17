"""Real restic -> authenticated rclone gateway -> two local union upstreams."""
import contextlib
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request

from vpsbackup import client, server
from vpsbackup.cli import restore
from vpsbackup import recovery
import tarfile
from vpsbackup.common import atomic_json, atomic_text, private_json, terminate


READY = os.name == "posix" and all(shutil.which(p) for p in ("rclone", "restic", "htpasswd"))


@unittest.skipUnless(READY, "Requires Linux, restic, rclone and htpasswd")
class GatewayRoundtrip(unittest.TestCase):
    def test_union_backup_deduplication_wal_restore_and_authentication(self):
        with tempfile.TemporaryDirectory(prefix="vps-backup-integration-") as temp:
            root = Path(temp)
            for name in ("one", "two", "source"):
                (root / name).mkdir()
            source = root / "source"
            payload = os.urandom(8 * 1024 * 1024)
            (source / "payload.bin").write_bytes(payload)
            atomic_text(root / "rclone.conf", "[one]\ntype = local\n[two]\ntype = local\n[union]\ntype = union\nupstreams = one:" + str(root / "one") + " two:" + str(root / "two") + "\ncreate_policy = ff\nsearch_policy = ff\naction_policy = epall\n")
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            server_path = root / "server.json"
            remote = os.environ.get("VPS_BACKUP_TEST_REMOTE", "union:repositories")
            remote_config = os.environ.get("VPS_BACKUP_TEST_RCLONE_CONFIG", str(root / "rclone.conf"))
            if remote != "union:repositories" and (":vps-backup-validation-" not in remote or ".." in remote):
                self.fail("Cloud tests require a dedicated vps-backup-validation-* prefix")
            server.setup(server_path, remote, remote_config,
                         f"http://127.0.0.1:{port}/", f"127.0.0.1:{port}")
            central = private_json(server_path)
            enrolled = server.enroll(central, "fixture-vps")
            bundle = private_json(enrolled["bundle"])
            bundle.update(state_dir=str(root / "state"), reserve_mib=64,
                          apps=[{"id": "app", "path": str(source), "sqlite": ["db.sqlite"]}])
            atomic_json(root / "client.json", bundle)
            database = sqlite3.connect(source / "db.sqlite")
            with contextlib.closing(database):
                database.execute("PRAGMA journal_mode=WAL")
                database.execute("CREATE TABLE items(value TEXT)")
                database.execute("INSERT INTO items VALUES ('committed WAL row')")
                database.commit()
                self.assertTrue((source / "db.sqlite-wal").exists())
                env = {k: v for k, v in os.environ.items() if not k.startswith("RCLONE_")}
                with subprocess.Popen(server.gateway_args(central), env=env, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL, start_new_session=True) as gateway:
                    try:
                        for _ in range(100):
                            try:
                                urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)
                            except urllib.error.HTTPError as exc:
                                self.assertEqual(exc.code, 401)
                                break
                            except OSError:
                                time.sleep(0.05)
                        else:
                            self.fail("Gateway did not start")
                        Path(bundle["state_dir"]).mkdir()
                        client.restic(bundle, "init")
                        first = client.run(bundle)
                        self.assertEqual(first["status"], "success", first)
                        second = client.run(bundle)
                        self.assertEqual(second["status"], "success", second)
                        self.assertLess(second["apps"][0]["data_added"], len(payload) // 10)
                        snapshot = first["apps"][0]["snapshot_id"]
                        recovered = root / "restore"
                        restore(bundle, snapshot, recovered)
                        self.assertEqual((recovered / str(source).lstrip("/") / "payload.bin").read_bytes(), payload)
                        live_db_copy = recovered / str(source).lstrip("/") / "db.sqlite"
                        self.assertFalse(live_db_copy.exists(), "Live database must be excluded")
                        snapshot_db = recovered / str(root / "state/work/sqlite/app/db.sqlite").lstrip("/")
                        with contextlib.closing(sqlite3.connect(snapshot_db)) as db:
                            self.assertEqual(db.execute("SELECT value FROM items").fetchall(), [("committed WAL row",)])
                            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone(), ("ok",))
                        client.restic(bundle, "check", "--read-data")
                        exported = root / "exported.tar.gz"
                        recovery.export(bundle, snapshot, target=exported)
                        with tarfile.open(exported) as tar:
                            self.assertEqual(tar.extractfile("app/payload.bin").read(), payload)
                            self.assertEqual(tar.extractfile("app/db.sqlite").read()[:16], b"SQLite format 3\x00")
                            self.assertFalse(any("work/sqlite" in item.name for item in tar))
                        native = dict(bundle, storage_mode="archive")
                        first_archive = client.run(native)
                        self.assertEqual(first_archive["status"], "success", first_archive)
                        second_archive = client.run(native)
                        self.assertEqual(second_archive["status"], "success", second_archive)
                        self.assertTrue(second_archive["apps"][0]["unchanged"], second_archive)
                        self.assertEqual(second_archive["apps"][0]["data_added"], 0)
                        downloaded = root / "native.tar.gz"
                        recovery.export(native, app="app", target=downloaded)
                        with tarfile.open(downloaded) as tar:
                            self.assertEqual(tar.extractfile("app/payload.bin").read(), payload)
                        (source / "file-added").write_text("changed")
                        changed = client.run(native)
                        self.assertEqual(changed["status"], "success", changed)
                        self.assertFalse(changed["apps"][0]["unchanged"])
                        self.assertEqual(len(recovery.snapshots(native)), 2)
                        # Native objects live in a separate subfolder and must not break restic.
                        client.restic(bundle, "check", "--read-data")
                        self.assertFalse((root / "state/work").exists())
                        if remote == "union:repositories":
                            self.assertTrue(list((root / "one").rglob("config")) or list((root / "two").rglob("config")))
                        terminate(gateway)
                        bundle["timeout_seconds"] = 3
                        interrupted = client.run(bundle)
                        self.assertEqual(interrupted["status"], "failed")
                        self.assertTrue(interrupted["temporary_files_cleaned"])
                        self.assertEqual((source / "payload.bin").read_bytes(), payload)
                    finally:
                        terminate(gateway)


if __name__ == "__main__":
    unittest.main()
