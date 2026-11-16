import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from vpsbackup import schedule
from vpsbackup.common import BackupError


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name).resolve()
        self.command = self.folder / "backup executable"
        self.command.touch()
        self.config = self.folder / "client.json"
        self.platform = SimpleNamespace(name="posix", geteuid=lambda: 0,
                                        access=lambda *args: True, X_OK=os.X_OK)

    def configure(self, mode, calendar="Wed *-*-* 07:00:00 Europe/Moscow"):
        with patch.object(schedule, "os", self.platform), patch.object(schedule, "UNIT_DIRECTORY", self.folder):
            return schedule.configure(self.config, mode, calendar, 20, str(self.command))

    def test_enable_change_calendar_and_disable_without_stopping_backup(self):
        with patch.object(schedule, "execute", return_value=b"") as execute:
            result = self.configure("both")
            self.assertTrue(result["timer_enabled"])
            unit = (self.folder / "vps-backup-client.service").read_text()
            self.assertIn(schedule.unit_argument(str(self.command)), unit)
            self.assertIn('--min-age-hours" "20"', unit)
            self.configure("systemd", "daily")
            self.assertIn("OnCalendar=daily", (self.folder / "vps-backup-client.timer").read_text())
            execute.assert_any_call(["systemctl", "restart", "vps-backup-client.timer"], timeout=30)
            execute.reset_mock()
            self.assertFalse(self.configure("n8n")["timer_enabled"])
            execute.assert_any_call(["systemctl", "disable", "--now", "vps-backup-client.timer"], timeout=30)
            self.assertFalse(any("vps-backup-client.service" in call.args[0] for call in execute.call_args_list))

    def test_unmanaged_unit_is_preserved(self):
        path = self.folder / "vps-backup-client.service"
        path.write_text("existing administrator unit")
        with patch.object(schedule, "execute", return_value=b"") as execute:
            with self.assertRaises(BackupError):
                self.configure("both")
        self.assertEqual(path.read_text(), "existing administrator unit")
        self.assertEqual(execute.call_count, 1)

    def test_reject_calendar_injection_and_escape_unit_expansion(self):
        for calendar in ("daily\nUnit=unrelated.service", "daily%H"):
            with patch.object(schedule, "execute") as execute:
                with self.assertRaises(BackupError):
                    self.configure("both", calendar)
            execute.assert_not_called()
        self.assertEqual(schedule.unit_argument('/a%h/$HOME/"file"'), '"/a%%h/$$HOME/\\"file\\""')
