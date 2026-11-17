"""Publish an installed runtime while preserving configuration and the gateway binary."""
import os
from pathlib import Path
import shutil
import time

from .common import BackupError, locked, private_json


def owned_install(runtime, command):
    if not runtime.exists() and not command.exists():
        return False
    if runtime.is_symlink() or command.is_symlink():
        raise BackupError("Installation paths are symlinks; leaving them untouched.")
    if not runtime.is_dir() or not (runtime / 'vpsbackup/cli.py').is_file() or not command.is_file():
        raise BackupError("The installation paths contain another tool or an incomplete install.\n"
                          "Keep a copy and inspect /usr/local/lib/vps-backup and /usr/local/bin/vps-backup before retrying.")
    wrapper = command.read_text()
    if 'from vpsbackup.cli import main' not in wrapper or 'PYTHONPATH=/usr/local/lib/vps-backup' not in wrapper:
        raise BackupError("The installed command does not belong to this tool; it was not overwritten.")
    return True


def publish(stage, wrapper, runtime, command, config_path, default_state):
    updating = owned_install(runtime, command)
    state = default_state
    if config_path.is_file():
        try:
            state = Path(private_json(config_path).get('state_dir', str(default_state)))
        except (BackupError, ValueError, TypeError):
            # A broken client config must not prevent reinstalling the command that repairs setup.
            state = default_state
    if not state.is_absolute():
        raise BackupError("Configured state_dir must be absolute; runtime was not changed.")
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with locked(state / 'run.lock'):
        if owned_install(runtime, command) != updating:
            raise BackupError("Installation changed while updating; please run the installer again.")
        previous = runtime.with_name(runtime.name + '.before-update-' + str(time.time_ns()))
        if updating:
            if (runtime / 'bin').is_dir():
                shutil.copytree(runtime / 'bin', stage / 'bin', dirs_exist_ok=True)
            os.rename(runtime, previous)
        try:
            os.rename(stage, runtime)
            os.replace(wrapper, command)
        except BaseException:
            if runtime.exists():
                os.rename(runtime, stage)
            if updating:
                os.rename(previous, runtime)
            raise
    return {'updated': updating, 'previous_runtime': str(previous) if updating else None}
