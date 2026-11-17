import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest

from vpsbackup import cli, presentation, server
from vpsbackup.common import BackupError, atomic_json, execute


class DiagnosticTests(unittest.TestCase):
    def test_failures_preserve_stdout_stderr_and_the_previous_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'last-error.log'
            command = [sys.executable, '-c', 'import sys; print("metadata details"); print("lock details",file=sys.stderr); sys.exit(11)']
            for _ in range(2):
                with self.assertRaises(BackupError) as error:
                    execute(command, diagnostics=log)
                self.assertIn(str(log), str(error.exception))
            history = list((log.parent / 'errors').glob('*.log'))
            self.assertEqual(len(history), 2)
            for saved in [log, *history]:
                text = saved.read_text()
                self.assertIn('metadata details', text)
                self.assertIn('lock details', text)
                if os.name == 'posix':
                    self.assertEqual(saved.stat().st_mode & 0o777, 0o600)

    def test_lock_error_explains_the_code_and_points_to_the_logs_command(self):
        text = presentation.error_text('restic failed (exit 11)', '/etc/vps-backup/client.json')
        self.assertIn('repository lock', text)
        self.assertIn('vps-backup logs', text)
        self.assertNotIn('state_dir/last-error.log', text)

    def test_enrollment_output_has_a_real_target_path_and_permission_command(self):
        text = presentation.human_result({'operation': 'enroll', 'bundle': '/etc/vps-backup/clients/backtobed.bundle.json'})
        self.assertIn('chmod 600 /etc/vps-backup/backtobed.bundle.json', text)
        self.assertIn('setup --bundle /etc/vps-backup/backtobed.bundle.json', text)
        self.assertNotIn('/path/to', text)

    def test_wrong_server_config_has_an_actionable_message(self):
        with self.assertRaisesRegex(BackupError, 'SERVER configuration'):
            server.enroll({'server_id': 'client'}, 'backtobed')

    def test_logs_command_reads_the_configured_state_without_exposing_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            config = {'version': 1, 'server_id': 'test', 'repository': 'rest:http://localhost:8787/test/',
                      'rest_username': 'test', 'rest_password': 'fixture-password', 'repository_password': 'fixture-key',
                      'state_dir': str(root), 'reserve_mib': 64}
            path = root / 'client.json'
            atomic_json(path, config)
            (root / 'last-error.log').write_text('actual restic error')
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(cli.main(['logs', '--config', str(path)]), 0)
            self.assertIn('actual restic error', output.getvalue())
            self.assertNotIn('fixture-password', output.getvalue())
