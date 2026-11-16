import contextlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import time
from urllib.parse import urlsplit

from .common import BackupError, ID, MIB, absolute, atomic_json, check_space, execute, locked, overlaps


def validate(config):
    if config.get("version") != 1 or not ID.fullmatch(config.get("server_id", "")):
        raise BackupError("Invalid config version or server_id")
    repository = config.get("repository", "")
    url = urlsplit(repository.removeprefix("rest:"))
    if not repository.startswith("rest:") or url.username or url.password or url.query or url.fragment:
        raise BackupError("Use a REST repository URL without embedded credentials")
    if url.scheme != "https" and not (url.scheme == "http" and url.hostname in ("127.0.0.1", "localhost", "::1")):
        raise BackupError("Remote repositories require HTTPS; HTTP is allowed only on loopback")
    if not url.hostname or not url.path.endswith("/"):
        raise BackupError("Repository URL must have a host and end with /")
    for key in ("repository_password", "rest_username", "rest_password"):
        if not isinstance(config.get(key), str) or not config[key]:
            raise BackupError("Missing " + key)
    state = absolute(config.get("state_dir", ""), "state_dir")
    reserve = config.get("reserve_mib", 512)
    if type(reserve) is not int or reserve < 64:
        raise BackupError("reserve_mib must be an integer >= 64")
    apps = config.get("apps", [])
    if not apps:
        raise BackupError("Configure at least one app")
    seen = set()
    roots = []
    for app in apps:
        name = app.get("id", "")
        if not ID.fullmatch(name) or name in seen:
            raise BackupError("App IDs must be unique lowercase identifiers")
        seen.add(name)
        source = absolute(app.get("path", ""), "Source")
        if overlaps(source, state) or any(overlaps(source, other) for other in roots):
            raise BackupError("Source directories must not overlap state or each other")
        roots.append(source)
        for relative in app.get("sqlite", []):
            path = Path(relative)
            if path.is_absolute() or ".." in path.parts or not path.parts:
                raise BackupError("SQLite paths must be relative without parent traversal")
            absolute(str(source / path), "SQLite path")
        for container in app.get("stop_containers", []):
            if not isinstance(container, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", container):
                raise BackupError("Invalid container name")
    return config


def restic(config, *arguments, monitor=True):
    env = dict(os.environ)
    # Do not inherit backend overrides or a password command from an interactive shell.
    for key in list(env):
        if key.startswith("RESTIC_") or key.startswith("RCLONE_"):
            del env[key]
    env.update(RESTIC_PASSWORD=config["repository_password"],
               RESTIC_REST_USERNAME=config["rest_username"],
               RESTIC_REST_PASSWORD=config["rest_password"], GOMAXPROCS="2")
    temp = Path(config["state_dir"]) / "work" / "tmp"
    if temp.is_dir():
        env["TMPDIR"] = str(temp)
    args = ["restic", "--repo", config["repository"], "--no-cache"]
    if config.get("ca_file"):
        args += ["--cacert", config["ca_file"]]
    args += list(arguments)
    return execute(args, env=env, timeout=config.get("timeout_seconds", 14400),
                   space=(config["state_dir"], config.get("reserve_mib", 512) * MIB) if monitor else None,
                   diagnostics=Path(config["state_dir"]) / "last-error.log")


def database_size(path):
    with contextlib.closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)) as db:
        return db.execute("PRAGMA page_count").fetchone()[0] * db.execute("PRAGMA page_size").fetchone()[0]


def doctor(config):
    validate(config)
    if not shutil.which("restic"):
        raise BackupError("Install restic")
    state = Path(config["state_dir"])
    # Find the filesystem without creating anything during offline checks.
    disk = state
    while not disk.exists():
        disk = disk.parent
    snapshot_bytes = 0
    stopped_bytes = 0
    apps = []
    for app in config["apps"]:
        source = Path(app["path"])
        if not source.is_dir():
            raise BackupError("Required source missing: " + app["id"])
        for relative in app.get("sqlite", []):
            path = source / relative
            if not path.is_file():
                raise BackupError("Required SQLite database missing: " + app["id"] + "/" + relative)
            snapshot_bytes += database_size(path)
        if app.get("stop_containers") and not shutil.which("docker"):
            raise BackupError("Docker required by " + app["id"])
        if app.get("stop_containers"):
            stopped_bytes += sum(p.stat().st_size for p in source.rglob("*") if p.is_file() and not p.is_symlink())
        apps.append({"id": app["id"], "path": str(source), "sqlite": app.get("sqlite", []),
                     "stop_containers": app.get("stop_containers", [])})
    required = (snapshot_bytes + stopped_bytes) * 2 + config.get("reserve_mib", 512) * MIB + 32 * MIB
    free = check_space(disk, required)
    return {"apps": apps, "snapshot_estimate_bytes": snapshot_bytes, "stopped_copy_estimate_bytes": stopped_bytes,
            "required_free_bytes": required,
            "free_bytes": free, "cache": "disabled"}


def sqlite_snapshot(source, target, state, reserve):
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    deadline = time.monotonic() + 120
    def progress(status, remaining, total):
        check_space(state, reserve)
        if time.monotonic() > deadline:
            raise BackupError("SQLite snapshot timed out")
    with contextlib.closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=10)) as src:
        with contextlib.closing(sqlite3.connect(target)) as dst:
            src.backup(dst, pages=256, progress=progress, sleep=0.05)
            if dst.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise BackupError("SQLite snapshot failed integrity_check")
    original = source.stat()
    os.chmod(target, original.st_mode & 0o777)
    if os.name == "posix":
        os.chown(target, original.st_uid, original.st_gid)


def resume(state):
    journal = state / "stopped.json"
    if not journal.exists():
        return
    from .common import private_json
    names = private_json(journal)["containers"]
    if any(not isinstance(n, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", n) for n in names):
        raise BackupError("Invalid container recovery journal")
    failed = []
    for name in reversed(names):
        try:
            execute(["docker", "start", name], timeout=60)
            running = execute(["docker", "inspect", "--format", "{{.State.Running}}", name], timeout=30)
            if running.strip() != b"true":
                raise BackupError("Container did not restart")
        except BackupError:
            failed.append(name)
    if failed:
        raise BackupError("Could not restart containers: " + ", ".join(failed))
    journal.unlink()


@contextlib.contextmanager
def stopped(app, state):
    names = []
    try:
        for name in app.get("stop_containers", []):
            if execute(["docker", "inspect", "--format", "{{.State.Running}}", name], timeout=30).strip() == b"true":
                names.append(name)
                atomic_json(state / "stopped.json", {"containers": names})
                execute(["docker", "stop", "--time", "30", name], timeout=60)
        yield
    finally:
        resume(state)


def clean_work(state):
    work = state / "work"
    if work.is_symlink():
        raise BackupError("Work directory must not be a symlink")
    if work.exists():
        shutil.rmtree(work)


def stage_stopped(source, target, state, reserve):
    def copy_file(src, dst):
        check_space(state, reserve + Path(src).stat().st_size)
        shutil.copy2(src, dst)
        if os.name == "posix":
            info = Path(src).stat()
            os.chown(dst, info.st_uid, info.st_gid)
        return dst
    shutil.copytree(source, target, symlinks=True, copy_function=copy_file)
    if os.name == "posix":
        for path in [target, *target.rglob("*")]:
            original = source / path.relative_to(target)
            info = original.lstat()
            os.chown(path, info.st_uid, info.st_gid, follow_symlinks=False)


def run(config):
    validate(config)
    state = Path(config["state_dir"])
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    result = {"server_id": config["server_id"], "status": "failed", "stage": "preflight", "apps": []}
    with locked(state / "run.lock"):
        try:
            # Recovery must run before preflight, which may itself fail.
            resume(state)
            clean_work(state)
            result["space"] = doctor(config)
            work = state / "work"
            work.mkdir(mode=0o700)
            (work / "tmp").mkdir(mode=0o700)
            for app in config["apps"]:
                item = {"id": app["id"], "status": "failed", "stage": "snapshot"}
                result["apps"].append(item)
                source = Path(app["path"])
                snapshot_root = work / "sqlite" / app["id"]
                args = ["backup", "--json", "--quiet", "--host", config["server_id"], "--tag", "app:" + app["id"]]
                with stopped(app, state):
                    if app.get("stop_containers"):
                        staged = work / "stopped" / app["id"]
                        stage_stopped(source, staged, state, config.get("reserve_mib", 512) * MIB)
                        source = staged
                    for relative in app.get("sqlite", []):
                        sqlite_snapshot(source / relative, snapshot_root / relative, state,
                                        config.get("reserve_mib", 512) * MIB)
                        if any(c in str(source / relative) for c in "*?[]\n\r"):
                            raise BackupError("SQLite paths containing glob characters are unsupported")
                        for suffix in ("", "-wal", "-shm", "-journal"):
                            args += ["--exclude", str(source / relative) + suffix]
                    args += [str(source)]
                    if app.get("sqlite"):
                        args += [str(snapshot_root)]
                item["stage"] = "backup"
                records = restic(config, *args).decode().splitlines()
                summaries = [json.loads(line) for line in records if line.strip()]
                summary = next((r for r in reversed(summaries) if r.get("message_type") == "summary"), None)
                if summary is None or not summary.get("snapshot_id"):
                    raise BackupError("Restic did not return a snapshot ID")
                item.update(status="success", stage="saved", snapshot_id=summary["snapshot_id"],
                            data_added=summary.get("data_added", 0), total_bytes=summary.get("total_bytes_processed", 0))
            result["stage"] = "check"
            # Check repository metadata after all app snapshots, without deleting anything.
            restic(config, "check")
            result.update(status="success", stage="complete")
        except Exception as exc:
            result["error"] = str(exc) if isinstance(exc, BackupError) else type(exc).__name__
        finally:
            try:
                clean_work(state)
            except Exception as exc:
                result.update(status="failed", cleanup_error=type(exc).__name__)
            result["temporary_files_cleaned"] = not (state / "work").exists()
            atomic_json(state / "last-run.json", result)
    return result
