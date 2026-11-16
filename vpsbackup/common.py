import contextlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import time


MIB = 1024 * 1024
ID = re.compile(r"[a-z0-9][a-z0-9-]{0,62}\Z")


class BackupError(Exception):
    pass


def atomic_json(path, value):
    atomic_text(path, json.dumps(value, indent=2) + "\n")


def atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            os.chmod(name, 0o600)
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def private_json(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise BackupError("Expected a regular configuration file: " + str(path))
    if os.name == "posix" and path.stat().st_mode & 0o077:
        raise BackupError("Configuration permissions must be 600: " + str(path))
    with path.open(encoding="utf-8") as source:
        return json.load(source)


def absolute(value, label):
    path = Path(value)
    if not path.is_absolute() or path.resolve() != path.absolute():
        raise BackupError(label + " must be absolute without symlinks or parent traversal")
    return path


def overlaps(first, second):
    return first == second or first in second.parents or second in first.parents


def check_space(path, required):
    free = shutil.disk_usage(path).free
    if free < required:
        raise BackupError(f"Insufficient free space: need {required} bytes; have {free}")
    return free


@contextlib.contextmanager
def locked(path):
    import fcntl
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BackupError("Another operation is running") from exc
        yield


def terminate(process):
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.wait()


def execute(args, *, env=None, timeout=3600, space=None, input_data=None, diagnostics=None):
    """Keep diagnostics private; callers return stages rather than credential-bearing URLs."""
    started = time.monotonic()
    try:
        with subprocess.Popen(args, env=env, stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              start_new_session=os.name == "posix") as process:
            try:
                first = True
                while True:
                    try:
                        output, errors = process.communicate(input=input_data if first else None, timeout=1)
                        break
                    except subprocess.TimeoutExpired:
                        first = False
                        if space:
                            check_space(*space)
                        if time.monotonic() - started > timeout:
                            raise BackupError(Path(args[0]).name + " timed out")
                if process.returncode:
                    if diagnostics:
                        atomic_text(diagnostics, errors[-65536:].decode(errors="replace"))
                    raise BackupError(Path(args[0]).name + " failed (exit " + str(process.returncode) + ")")
                if len(output) > 16 * MIB:
                    raise BackupError("Command output exceeded 16 MiB")
                return output
            finally:
                terminate(process)
    except OSError as exc:
        raise BackupError("Cannot run " + Path(args[0]).name) from exc
