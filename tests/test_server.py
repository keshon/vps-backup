import json
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from vpsbackup import server
from vpsbackup.common import BackupError, atomic_text, private_json


class CloudSpaceTests(unittest.TestCase):
    def test_capacity_is_read_from_server_and_missing_fields_stay_unknown(self):
        config = {"rclone_binary": "rclone", "rclone_config": "private.conf", "remote": "union:backups"}
        with patch.object(server, "execute", return_value=b'{"total":100,"free":30}') as execute:
            result = server.cloud_space(config)
        self.assertEqual(result["free_bytes"], 30)
        self.assertNotIn("used_bytes", result)
        self.assertIn("private.conf", execute.call_args.args[0])
        self.assertFalse(any(key.startswith("RCLONE_") for key in execute.call_args.kwargs["env"]))

    def test_invalid_or_unsupported_capacity_fails(self):
        config = {"rclone_binary": "rclone", "rclone_config": "private.conf", "remote": "union:backups"}
        for raw in (b'{}', b'[]', b'{"free":-1}', b'{"free":true}', b'not json'):
            with self.subTest(raw=raw), patch.object(server, "execute", return_value=raw):
                with self.assertRaises(BackupError):
                    server.cloud_space(config)


class EnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        atomic_text(self.root / "htpasswd", "")
        self.config = {"clients_dir": str(self.root / "clients"), "htpasswd": str(self.root / "htpasswd"),
                       "public_url": "https://backup.example.com/"}
        p = patch.object(server, "locked")
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(server, "password_entry", side_effect=lambda user, password: user + ":bcrypt-fixture\n")
        p.start()
        self.addCleanup(p.stop)

    def test_enrollment_has_unique_passwords_and_no_cloud_credentials(self):
        first = server.enroll(self.config, "server-a")
        second = server.enroll(self.config, "server-b")
        a = private_json(first["bundle"])
        b = private_json(second["bundle"])
        self.assertNotEqual(a["repository_password"], b["repository_password"])
        self.assertNotEqual(a["rest_password"], b["rest_password"])
        self.assertNotEqual(a["rest_password"], a["repository_password"])
        self.assertEqual(a["repository"], "rest:https://backup.example.com/server-a/")
        self.assertNotIn("rclone", json.dumps(a))

    def test_reenrollment_does_not_destroy_recovery_credentials(self):
        result = server.enroll(self.config, "server-a")
        before = Path(result["bundle"]).read_bytes()
        with self.assertRaises(BackupError):
            server.enroll(self.config, "server-a")
        self.assertEqual(Path(result["bundle"]).read_bytes(), before)

    def test_private_proxy_binding_is_explicit_and_never_wildcard(self):
        binary = self.root / "rclone"
        binary.write_bytes(b"fixture")
        config = {"rclone_binary": str(binary), "audited_rclone_sha256": hashlib.sha256(b"fixture").hexdigest(),
                  "rclone_config": "private.conf", "remote": "union:new-prefix", "htpasswd": "htpasswd",
                  "listen": "172.19.0.1:8787"}
        with self.assertRaises(BackupError):
            server.gateway_args(config)
        config["trusted_proxy"] = True
        self.assertIn("172.19.0.1:8787", server.gateway_args(config))
        for address in ("0.0.0.0:8787", "1.1.1.1:8787", "[::]:8787"):
            config["listen"] = address
            with self.assertRaises(BackupError):
                server.gateway_args(config)


if __name__ == "__main__":
    unittest.main()
