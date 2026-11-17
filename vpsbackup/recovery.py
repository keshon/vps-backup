"""Read backups and produce portable archives without extracting Linux files locally."""
import contextlib
import copy
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile
import threading
import time

from . import archive, client
from .common import BackupError, ID, MIB, check_space, overlaps, terminate


def snapshots(config, mode=None, repository=None):
    mode = mode or config.get("storage_mode", "restic")
    if mode == "archive":
        if repository:
            raise BackupError("A downloaded restic repository requires --mode restic")
        return archive.snapshots(config)
    return json.loads(client.restic(config, "snapshots", "--json", repository=repository))


def select(items, snapshot="latest", app=None):
    if app is not None and not ID.fullmatch(app):
        raise BackupError("Invalid app ID")
    if snapshot != "latest" and not re.fullmatch(r"[a-f0-9]{8,64}", snapshot):
        raise BackupError("Use latest or an explicit snapshot ID")
    items = [s for s in items if app is None or "app:" + app in s.get("tags", [])]
    if snapshot != "latest":
        items = [s for s in items if s["id"].startswith(snapshot)]
        if len(items) > 1:
            raise BackupError("Snapshot prefix is ambiguous; use its full ID")
    elif app is None and len({t for s in items for t in s.get("tags", []) if t.startswith("app:")}) > 1:
        raise BackupError("Choose an app with --app, or select a snapshot ID")
    if not items:
        raise BackupError("No matching backup found")
    return max(items, key=lambda s: s["time"])


@contextlib.contextmanager
def dump_stream(config, snapshot, repository, disk):
    args, env = client.restic_command(config, ("dump", "--archive", "tar", snapshot, "/"), repository)
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(args, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=errors, start_new_session=os.name == "posix")
        stop = threading.Event()
        failures = []
        started = time.monotonic()

        def monitor():
            while not stop.wait(0.5):
                try:
                    check_space(disk, config.get("reserve_mib", 512) * MIB)
                    if time.monotonic() - started > config.get("timeout_seconds", 14400):
                        raise BackupError("Archive export timed out")
                except Exception as exc:
                    failures.append(exc)
                    terminate(process)
                    break

        watcher = threading.Thread(target=monitor)
        watcher.start()
        try:
            yield process.stdout
            while process.stdout.read(MIB):
                pass
            code = process.wait(timeout=30)
            if failures:
                raise failures[0]
            if code:
                raise BackupError("Restic export failed; check repository access and free space")
        except Exception:
            if failures:
                raise failures[0]
            raise
        finally:
            stop.set()
            terminate(process)
            watcher.join()
            process.stdout.close()


def archive_path(name, roots, app):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise BackupError("Unsafe path in snapshot archive")
    for root in roots:
        if path == root or root in path.parents:
            return (PurePosixPath(app) / path.relative_to(root)).as_posix()
    return None


def convert_tar(source, destination, snapshot, app):
    roots = [PurePosixPath(p.lstrip("/")) for p in snapshot["paths"]]
    if not roots or len(roots) > 2 or any(".." in p.parts or not p.parts for p in roots):
        raise BackupError("Unsupported snapshot layout")
    count = 0
    names = set()
    with tarfile.open(fileobj=source, mode="r|") as incoming:
        with tarfile.open(fileobj=destination, mode="w:gz") as output:
            for member in incoming:
                name = archive_path(member.name, roots, app)
                if name is None:
                    if member.isdir():
                        continue
                    raise BackupError("File outside the app snapshot roots")
                if name in names:
                    if member.isdir():
                        continue
                    raise BackupError("Duplicate output file in snapshot")
                names.add(name)
                info = copy.copy(member)
                info.name = name
                info.pax_headers = {k: v for k, v in member.pax_headers.items() if k not in ("path", "linkpath")}
                if info.islnk():
                    info.linkname = archive_path(info.linkname, roots, app)
                    if info.linkname is None:
                        raise BackupError("Hard link outside the app snapshot")
                if member.isfile():
                    with incoming.extractfile(member) as contents:
                        output.addfile(info, contents)
                else:
                    output.addfile(info)
                count += int(member.isfile())
            if not count:
                raise BackupError("Snapshot did not contain any app files")


def export(config, snapshot="latest", app=None, target=None, mode=None, repository=None):
    mode = mode or config.get("storage_mode", "restic")
    saved = select(snapshots(config, mode, repository), snapshot, app)
    tags = [t[4:] for t in saved.get("tags", []) if t.startswith("app:")]
    if len(tags) != 1 or not ID.fullmatch(tags[0]):
        raise BackupError("Snapshot must identify exactly one app")
    app = tags[0]
    if target is None:
        date = re.sub(r"[^0-9]", "", saved["time"][:19])
        target = Path.cwd() / (app + "-" + date + "-" + saved["id"][:8] + ".tar.gz")
    target = Path(target).expanduser().resolve()
    if not target.name.endswith(".tar.gz"):
        raise BackupError("Use a .tar.gz output filename")
    if not target.parent.is_dir():
        raise BackupError("Archive destination directory does not exist")
    if overlaps(target, Path(config["state_dir"])):
        raise BackupError("Archive destination must be outside temporary backup state")
    for entry in config.get("apps", []):
        path = Path(entry["path"])
        if path.is_absolute() and overlaps(target, path):
            raise BackupError("Archive destination must be outside live app folders")
    if target.exists() or target.is_symlink():
        raise BackupError("Archive destination already exists; choose another filename")
    check_space(target.parent, config.get("reserve_mib", 512) * MIB)
    # The final filename appears only after the whole export has succeeded.
    fd, temporary = tempfile.mkstemp(prefix=".vps-export-", dir=target.parent)
    temp = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as destination:
            os.chmod(temp, 0o600)
            if mode == "archive":
                archive.verify(config, "data/" + saved["archive"], saved["sha256"], destination, target.parent)
            else:
                with dump_stream(config, saved["id"], repository, target.parent) as source:
                    convert_tar(source, destination, saved, app)
            destination.flush()
            os.fsync(destination.fileno())
        # Hard-link publication is atomic and refuses an existing destination.
        os.link(temp, target)
        return {"status": "success", "operation": "export", "archive": str(target),
                "snapshot_id": saved["id"], "app": app, "bytes": target.stat().st_size}
    finally:
        temp.unlink(missing_ok=True)
