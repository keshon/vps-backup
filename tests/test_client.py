import contextlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from vpsbackup import client
from vpsbackup.cli import restore
from vpsbackup.common import BackupError, MIB


@contextlib.contextmanager
def unlocked(path):
    yield


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "app"
        self.source.mkdir()
        (self.source / "file.txt").write_text("original")
        self.state = self.root / "state"
        self.config = {"version": 1, "server_id": "server-a", "repository": "rest:http://127.0.0.1:8787/server-a/",
                       "rest_username": "server-a", "rest_password": "auth-secret", "repository_password": "encryption-secret",
                       "state_dir": str(self.state), "reserve_mib": 64,
                       "apps": [{"id": "app", "path": str(self.source)}]}
        for name, value in (("locked", unlocked),):
            p = patch.object(client, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(client.shutil, "which", return_value="fixture-program")
        p.start()
        self.addCleanup(p.stop)

    def success(self, config, *args):
        if args[0] == "backup":
            return json.dumps({"message_type": "summary", "snapshot_id": "a" * 64, "data_added": 42}).encode()
        return b""

    def test_low_space_refuses_before_backup_and_cleans_stale_work(self):
        (self.state / "work").mkdir(parents=True)
        (self.state / "work" / "old").write_text("stale")
        with patch.object(client, "check_space", side_effect=BackupError("Insufficient free space")), patch.object(client, "restic") as restic:
            result = client.run(self.config)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["temporary_files_cleaned"])
        restic.assert_not_called()

    def test_failure_preserves_sources_and_removes_snapshots(self):
        self.create_database()
        with patch.object(client, "restic", side_effect=BackupError("upload unavailable")):
            result = client.run(self.config)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["temporary_files_cleaned"])
        self.assertEqual((self.source / "file.txt").read_text(), "original")
        with contextlib.closing(sqlite3.connect(self.source / "db.sqlite")) as db:
            self.assertEqual(db.execute("SELECT value FROM items").fetchall(), [("saved",)])

    def create_database(self):
        path = self.source / "db.sqlite"
        with contextlib.closing(sqlite3.connect(path)) as db:
            db.execute("CREATE TABLE items(value TEXT)")
            db.execute("INSERT INTO items VALUES ('saved')")
            db.commit()
        self.config["apps"][0]["sqlite"] = ["db.sqlite"]

    def test_wal_snapshot_contains_committed_data(self):
        db = sqlite3.connect(self.source / "wal.sqlite")
        self.addCleanup(db.close)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE items(value TEXT)")
        db.execute("INSERT INTO items VALUES ('from wal')")
        db.commit()
        snapshot = self.root / "snapshot.sqlite"
        client.sqlite_snapshot(self.source / "wal.sqlite", snapshot, self.root, 0)
        with contextlib.closing(sqlite3.connect(snapshot)) as copy:
            self.assertEqual(copy.execute("SELECT value FROM items").fetchall(), [("from wal",)])

    def test_sqlite_snapshot_is_uploaded_and_live_files_are_excluded(self):
        self.create_database()
        def record(config, *args):
            if args[0] == "backup":
                snapshot = self.state / "work/sqlite/app/db.sqlite"
                self.assertTrue(snapshot.is_file())
                for suffix in ("", "-wal", "-shm", "-journal"):
                    self.assertIn(str(self.source / "db.sqlite") + suffix, args)
            return self.success(config, *args)
        with patch.object(client, "restic", side_effect=record):
            result = client.run(self.config)
        self.assertEqual(result["status"], "success")
        self.assertFalse((self.state / "work").exists())

    def test_stopped_container_is_resumed_before_network_backup(self):
        self.config["apps"][0]["stop_containers"] = ["portainer"]
        commands = []
        def docker(args, **kwargs):
            commands.append(args[:2])
            return b"true\n" if args[1] == "inspect" else b""
        def upload(config, *args):
            if args[0] == "backup":
                self.assertIn(["docker", "start"], commands)
                self.assertTrue((self.state / "work/stopped/app/file.txt").exists())
                self.assertFalse((self.state / "stopped.json").exists())
            return self.success(config, *args)
        with patch.object(client, "execute", side_effect=docker), patch.object(client, "restic", side_effect=upload):
            result = client.run(self.config)
        self.assertEqual(result["status"], "success")

    def test_restart_failure_preserves_recovery_journal(self):
        self.state.mkdir()
        from vpsbackup.common import atomic_json
        atomic_json(self.state / "stopped.json", {"containers": ["portainer"]})
        with patch.object(client, "execute", side_effect=BackupError("restart failed")):
            result = client.run(self.config)
        self.assertEqual(result["status"], "failed")
        self.assertTrue((self.state / "stopped.json").exists())

    def test_metadata_check_failure_is_not_reported_as_success(self):
        def fail_check(config, *args):
            if args[0] == "check":
                raise BackupError("check failed")
            return self.success(config, *args)
        with patch.object(client, "restic", side_effect=fail_check):
            result = client.run(self.config)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["stage"], "check")

    def test_restart_failure_does_not_prevent_other_container_restarts(self):
        self.state.mkdir()
        from vpsbackup.common import atomic_json
        atomic_json(self.state / "stopped.json", {"containers": ["first", "second"]})
        def restart(args, **kwargs):
            if args[1:] == ["start", "second"]:
                raise BackupError("Injected failure")
            return b"true\n"
        with patch.object(client, "execute", side_effect=restart) as command:
            with self.assertRaises(BackupError):
                client.resume(self.state)
        self.assertIn(unittest.mock.call(["docker", "start", "first"], timeout=60), command.call_args_list)
        self.assertTrue((self.state / "stopped.json").exists())

    def test_no_snapshot_id_is_a_failure(self):
        with patch.object(client, "restic", return_value=b"{}"):
            self.assertEqual(client.run(self.config)["status"], "failed")

    def test_no_remote_pruning_commands_in_run(self):
        with patch.object(client, "restic", side_effect=self.success) as run:
            self.assertEqual(client.run(self.config)["status"], "success")
        self.assertEqual([c.args[1] for c in run.call_args_list], ["backup", "check"])

    def test_reject_plaintext_remote_and_embedded_password(self):
        for url in ("rest:http://example.com/server/", "rest:https://user:password@example.com/server/"):
            self.config["repository"] = url
            with self.assertRaises(BackupError):
                client.validate(self.config)

    def test_reject_sources_overlapping_scratch(self):
        self.config["state_dir"] = str(self.source / "scratch")
        with self.assertRaises(BackupError):
            client.validate(self.config)

    def test_restore_refuses_production_and_nonempty_destination(self):
        for path in (self.source, self.root):
            with self.assertRaises(BackupError):
                restore(self.config, "a" * 64, path)

    def test_backend_environment_is_scrubbed_and_secrets_not_in_arguments(self):
        with patch.dict(os.environ, {"RESTIC_PASSWORD_COMMAND": "bad-command", "RCLONE_CONFIG": "bad-file"}), patch.object(client, "execute", return_value=b"") as call:
            client.restic(self.config, "snapshots")
        args = call.call_args.args[0]
        env = call.call_args.kwargs["env"]
        self.assertNotIn("auth-secret", args)
        self.assertNotIn("encryption-secret", args)
        self.assertNotIn("RESTIC_PASSWORD_COMMAND", env)
        self.assertNotIn("RCLONE_CONFIG", env)
        self.assertEqual(env["RESTIC_PASSWORD"], "encryption-secret")


if __name__ == "__main__":
    unittest.main()
