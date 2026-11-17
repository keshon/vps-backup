import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from vpsbackup import cli, installation, onboarding
from vpsbackup.common import BackupError, atomic_json, private_json


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / 'apps/app'
        self.source.mkdir(parents=True)
        self.config_path = self.root / 'client.json'
        self.bundle_path = self.root / 'server.bundle.json'
        self.bundle = {'version': 1, 'server_id': 'server', 'repository': 'rest:http://localhost:8787/server/',
                       'rest_username': 'server', 'rest_password': 'fixture-auth', 'repository_password': 'fixture-key',
                       'state_dir': str(self.root / 'state'), 'reserve_mib': 64, 'apps': []}
        atomic_json(self.bundle_path, self.bundle)
        self.apps_file = self.root / 'apps.json'
        atomic_json(self.apps_file, [{'id': 'app', 'path': str(self.source)}])
        for name, value in (('locked', lambda path: contextlib.nullcontext()),):
            fixture = patch.object(onboarding, name, value)
            fixture.start()
            self.addCleanup(fixture.stop)
        fixture = patch.object(onboarding.client, 'doctor', return_value={})
        fixture.start()
        self.addCleanup(fixture.stop)

    def setup(self, **options):
        return onboarding.setup_client(self.bundle_path, self.config_path, self.source.parent,
                                       apps_file=self.apps_file, **options)

    def test_missing_key_explains_origin_and_does_not_create_config(self):
        with self.assertRaisesRegex(BackupError, 'Cannot find the recovery key'):
            onboarding.setup_client(self.root / 'invented.json', self.config_path, self.source.parent)
        self.assertFalse(self.config_path.exists())

    def test_failed_initialization_can_resume_with_the_same_credentials(self):
        with patch.object(onboarding, 'repository_ready', side_effect=BackupError('network unavailable')):
            with self.assertRaises(BackupError):
                self.setup()
        saved = private_json(self.config_path)
        self.assertEqual(saved['repository_password'], self.bundle['repository_password'])
        with patch.object(onboarding, 'repository_ready', return_value=False), patch.object(onboarding.client, 'restic') as restic:
            result = onboarding.setup_client(None, self.config_path, self.source.parent)
        self.assertTrue(result['ready'])
        self.assertEqual(restic.call_args.args[1:], ('init',))
        self.assertEqual(private_json(self.config_path), saved)

    def test_existing_repository_is_opened_not_reinitialized(self):
        with patch.object(onboarding, 'repository_ready', return_value=True), patch.object(onboarding.client, 'restic') as restic:
            self.setup()
        self.assertEqual(restic.call_args.args[1:], ('snapshots', '--json'))

    def test_key_copied_to_client_config_is_completed_without_replacing_the_key(self):
        atomic_json(self.config_path, self.bundle)
        with patch.object(onboarding, 'initialize_repository'):
            self.setup()
        self.assertEqual(private_json(self.config_path)['repository_password'], self.bundle['repository_password'])
        originals = list(self.root.glob('client.json.before-setup-*'))
        self.assertEqual(len(originals), 1)
        self.assertEqual(private_json(originals[0]), self.bundle)

    def test_different_key_cannot_replace_existing_config(self):
        configured = dict(self.bundle, apps=[{'id': 'app', 'path': str(self.source)}])
        atomic_json(self.config_path, configured)
        atomic_json(self.bundle_path, dict(self.bundle, repository_password='different-key'))
        with self.assertRaisesRegex(BackupError, 'different recovery key'):
            self.setup()
        self.assertEqual(private_json(self.config_path), configured)

    def test_bad_json_and_permissions_have_actionable_errors(self):
        self.config_path.write_text('')
        self.config_path.chmod(0o600)
        with self.assertRaisesRegex(BackupError, 'is empty'):
            onboarding.read_config(self.config_path)
        self.config_path.write_text('not JSON')
        with self.assertRaisesRegex(BackupError, 'not valid JSON'):
            onboarding.read_config(self.config_path)
        if os.name == 'posix':
            self.config_path.chmod(0o644)
            with self.assertRaisesRegex(BackupError, 'chmod 600'):
                onboarding.read_config(self.config_path)

    def test_auth_failure_is_not_treated_as_an_empty_repository(self):
        error = urllib.error.HTTPError('http://localhost/', 401, 'Unauthorized', {}, io.BytesIO())
        with patch.object(onboarding.urllib.request, 'urlopen', side_effect=error):
            with self.assertRaisesRegex(BackupError, 'rejected this recovery key'):
                onboarding.initialize_repository(self.bundle)

    def test_cancelled_app_selection_preserves_config(self):
        configured = dict(self.bundle, apps=[{'id': 'app', 'path': str(self.source)}])
        atomic_json(self.config_path, configured)
        with patch.object(onboarding, 'choose_apps', side_effect=BackupError('cancelled')):
            with self.assertRaises(BackupError):
                onboarding.select_apps(configured, self.config_path, self.source.parent)
        self.assertEqual(private_json(self.config_path), configured)

    def test_wizard_suggests_the_actual_writer_for_a_custom_wal_store(self):
        data = self.source / 'data'
        data.mkdir()
        (data / 'wal.log').write_text('fixture')
        (data / 'snapshot-001.json').write_text('{}')
        answers = iter(['y', '', 'y'])
        with patch.object(onboarding.sys.stdin, 'isatty', return_value=True), \
                patch.object(onboarding, 'container_mounts', return_value=[('app-container', data)]), \
                patch('builtins.input', side_effect=lambda: next(answers)), contextlib.redirect_stderr(io.StringIO()):
            apps = onboarding.choose_apps(self.source.parent)
        self.assertEqual(apps[0]['stop_containers'], ['app-container'])
        self.assertNotIn('custom_store', apps[0])


class CLIExperienceTests(unittest.TestCase):
    def test_config_and_json_flags_work_before_and_after_command_and_old_names_still_parse(self):
        for command, action in (('backup', 'run'), ('run', 'run'), ('setup', 'setup-client'),
                                ('setup-client', 'setup-client'), ('verify', 'check'), ('check', 'check')):
            for args in (['--config', '/tmp/custom.json', '--json', command],
                         [command, '--config', '/tmp/custom.json', '--json']):
                parsed = cli.parser().parse_args(args)
                self.assertEqual(parsed.action, action)
                self.assertEqual(parsed.config, Path('/tmp/custom.json'))
                self.assertTrue(parsed.json)

    def test_help_has_categories_and_real_program_name(self):
        help_text = cli.parser().format_help()
        self.assertIn('Getting started', help_text)
        self.assertIn('Everyday use', help_text)
        self.assertIn('Backup-server administration', help_text)
        self.assertNotIn('usage: -c', help_text)

    def test_missing_setup_prints_next_step_and_json_output_keeps_it_for_scripts(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = str(Path(directory) / 'client.json')
            for terminal in (True, False):
                out, err = io.StringIO(), io.StringIO()
                with patch('sys.stdout', out), patch('sys.stderr', err), patch.object(out, 'isatty', return_value=terminal):
                    code = cli.main(['backup', '--config', missing])
                self.assertEqual(code, 1)
                if terminal:
                    self.assertIn('vps-backup setup', err.getvalue())
                    self.assertEqual(out.getvalue(), '')
                else:
                    result = json.loads(out.getvalue())
                    self.assertEqual(result['status'], 'failed')
                    self.assertIn('vps-backup setup', result['hint'])

    def test_server_commands_choose_server_configuration_automatically(self):
        with patch.object(onboarding, 'read_config', return_value={}) as read, \
                patch.object(cli.server, 'cloud_space', return_value={'status': 'success'}), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(['cloud-space']), 0)
        self.assertEqual(read.call_args.args[0], Path('/etc/vps-backup/server.json'))

    def test_json_flag_wins_even_in_a_terminal(self):
        output = io.StringIO()
        with patch.object(onboarding, 'read_config', return_value={}), \
                patch.object(cli.server, 'cloud_space', return_value={'status': 'success', 'free_bytes': 100}), \
                patch('sys.stdout', output), patch.object(output, 'isatty', return_value=True):
            self.assertEqual(cli.main(['cloud-space', '--json']), 0)
        self.assertEqual(json.loads(output.getvalue())['free_bytes'], 100)


class InstallationTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'posix', 'Uses Linux installation locking')
    def test_update_preserves_config_gateway_and_previous_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            runtime, stage = root / 'runtime', root / 'stage'
            for folder, content in ((runtime, 'old'), (stage, 'new')):
                (folder / 'vpsbackup').mkdir(parents=True)
                (folder / 'vpsbackup/cli.py').write_text(content)
            (runtime / 'bin').mkdir()
            (runtime / 'bin/rclone').write_bytes(b'gateway')
            command, wrapper = root / 'command', root / 'wrapper'
            command.write_text('PYTHONPATH=/usr/local/lib/vps-backup\nfrom vpsbackup.cli import main')
            wrapper.write_text(command.read_text() + '\nnew')
            config_path = root / 'client.json'
            atomic_json(config_path, {'state_dir': str(root / 'state'), 'secret': 'keep'})
            before = config_path.read_bytes()
            result = installation.publish(stage, wrapper, runtime, command, config_path, root / 'state')
            self.assertTrue(result['updated'])
            self.assertEqual((runtime / 'vpsbackup/cli.py').read_text(), 'new')
            self.assertEqual((runtime / 'bin/rclone').read_bytes(), b'gateway')
            self.assertEqual(config_path.read_bytes(), before)
            self.assertEqual((Path(result['previous_runtime']) / 'vpsbackup/cli.py').read_text(), 'old')

    def test_other_tools_are_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command = root / 'command'
            command.write_text('another tool')
            with self.assertRaises(BackupError):
                installation.owned_install(root / 'runtime', command)
            self.assertEqual(command.read_text(), 'another tool')

    @unittest.skipUnless(os.name == 'posix', 'Uses Linux installation locking')
    def test_failed_wrapper_publication_restores_the_previous_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            runtime, stage = root / 'runtime', root / 'stage'
            for folder, content in ((runtime, 'old'), (stage, 'new')):
                (folder / 'vpsbackup').mkdir(parents=True)
                (folder / 'vpsbackup/cli.py').write_text(content)
            command = root / 'command'
            command.write_text('PYTHONPATH=/usr/local/lib/vps-backup\nfrom vpsbackup.cli import main')
            with self.assertRaises(FileNotFoundError):
                installation.publish(stage, root / 'missing-wrapper', runtime, command, root / 'no-config', root / 'state')
            self.assertEqual((runtime / 'vpsbackup/cli.py').read_text(), 'old')
            self.assertEqual((stage / 'vpsbackup/cli.py').read_text(), 'new')
