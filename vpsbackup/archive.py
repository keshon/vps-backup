"""Standalone app archives through the same authenticated, append-only gateway."""
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import ssl
import tarfile
import urllib.error
import urllib.request
import uuid

from .common import BackupError, ID, MIB, check_space


def request(config, method, path, data=None, size=None):
    url = config["repository"].removeprefix("rest:").rstrip("/") + "/archives/" + path
    auth = (config["rest_username"] + ":" + config["rest_password"]).encode()
    headers = {"Authorization": "Basic " + base64.b64encode(auth).decode(),
               "Accept": "application/vnd.x.restic.rest.v2"}
    if size is not None:
        headers["Content-Length"] = str(size)
    context = ssl.create_default_context(cafile=config.get("ca_file"))
    try:
        return urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers, method=method),
                                      timeout=config.get("timeout_seconds", 14400), context=context)
    except urllib.error.HTTPError as exc:
        exc.close()
        if exc.code == 404 and method in ("GET", "HEAD"):
            return None
        raise BackupError("Archive gateway " + method + " " + path + " HTTP " + str(exc.code)) from None
    except (OSError, urllib.error.URLError):
        raise BackupError("Cannot reach the archive gateway") from None


def object_name(name, suffix):
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9-]+" + re.escape(suffix), name):
        raise BackupError("Invalid archive object name")
    return name


def read_json(response, limit):
    with response:
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise BackupError("Archive gateway JSON exceeds its size limit")
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise BackupError("Invalid JSON from archive gateway") from exc


def validate_manifest(item, name):
    if not isinstance(item, dict):
        raise BackupError("Invalid archive manifest")
    identity = item.get("id")
    app = item.get("app")
    size = item.get("size")
    if (item.get("format") != "vps-backup-archive-v1"
            or not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9]{64}", identity)
            or name != identity + ".json"
            or not isinstance(app, str) or not ID.fullmatch(app)
            or item.get("tags") != ["app:" + app]
            or not isinstance(size, int) or isinstance(size, bool) or size < 0):
        raise BackupError("Invalid archive manifest")
    for field in ("sha256", "fingerprint"):
        value = item.get(field)
        if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
            raise BackupError("Invalid archive manifest checksum")
    try:
        timestamp = datetime.fromisoformat(item["time"])
    except (KeyError, TypeError, ValueError) as exc:
        raise BackupError("Invalid archive manifest time") from exc
    if timestamp.tzinfo is None:
        raise BackupError("Archive manifest time requires a timezone")
    object_name(item.get("archive"), ".tar.gz")
    return item


def snapshots(config):
    response = request(config, "GET", "snapshots/")
    if response is None:
        return []
    listing = read_json(response, 16 * MIB)
    if not isinstance(listing, list):
        raise BackupError("Invalid archive snapshot listing")
    result = []
    for entry in listing:
        if not isinstance(entry, dict):
            raise BackupError("Invalid archive snapshot listing")
        name = object_name(entry.get("name"), ".json")
        response = request(config, "GET", "snapshots/" + name)
        if response is None:
            raise BackupError("An archive manifest disappeared")
        item = read_json(response, MIB)
        result.append(validate_manifest(item, name))
    return result


class ContentReader:
    def __init__(self, handle, state, reserve):
        self.handle = handle
        self.state = state
        self.reserve = reserve
        self.digest = hashlib.sha256()

    def read(self, size=-1):
        check_space(self.state, self.reserve)
        data = self.handle.read(size)
        self.digest.update(data)
        return data


def build(app, source, sqlite_root, target, state, reserve):
    """Hash the same bytes written to the tar; ignore timestamps for change detection."""
    fingerprint = hashlib.sha256()
    excluded = {relative + suffix for relative in app.get("sqlite", [])
                for suffix in ("", "-wal", "-shm", "-journal")}
    files = [(source, PurePosixPath("."))]
    files += [(p, PurePosixPath(p.relative_to(source).as_posix())) for p in sorted(source.rglob("*"))]
    if app.get("sqlite"):
        files += [(sqlite_root / relative, PurePosixPath(relative)) for relative in sorted(app["sqlite"])]
    names = set()
    with tarfile.open(target, "w:gz", dereference=False) as output:
        for path, relative in files:
            if relative.as_posix() in excluded and not path.is_relative_to(sqlite_root):
                continue
            name = (PurePosixPath(app["id"]) / relative).as_posix()
            if name in names:
                raise BackupError("Duplicate file in archive: " + name)
            names.add(name)
            before = path.lstat()
            info = output.gettarinfo(str(path), arcname=name)
            if not (info.isfile() or info.isdir() or info.issym() or info.islnk()):
                raise BackupError("Unsupported special file in archive: " + name)
            content = ""
            if info.isfile():
                with path.open("rb") as handle:
                    reader = ContentReader(handle, state, reserve)
                    output.addfile(info, reader)
                    content = reader.digest.hexdigest()
            else:
                output.addfile(info)
            after = path.lstat()
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_ino) != (
                    after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_ino):
                raise BackupError("File changed while archiving: " + name + "; retry or configure its container to stop")
            fingerprint.update(json.dumps([name, info.type.decode(), info.mode, info.uid, info.gid,
                                           info.linkname, content], separators=(",", ":")).encode())
    return fingerprint.hexdigest()


def digest_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(MIB), b""):
            digest.update(block)
    return digest.hexdigest()


def verify(config, path, expected, target=None, disk=None):
    response = request(config, "GET", path)
    if response is None:
        raise BackupError("Archive object is missing")
    digest = hashlib.sha256()
    with response:
        while True:
            data = response.read(MIB)
            if not data:
                break
            if target is not None:
                check_space(disk or Path(config["state_dir"]), config.get("reserve_mib", 512) * MIB)
                target.write(data)
            digest.update(data)
    if digest.hexdigest() != expected:
        raise BackupError("Archive checksum verification failed")


def save(config, app, source, sqlite_root):
    state = Path(config["state_dir"])
    target = state / "work" / (app["id"] + ".tar.gz")
    fingerprint = build(app, source, sqlite_root, target, state, config.get("reserve_mib", 512) * MIB)
    previous = sorted((s for s in snapshots(config) if s["app"] == app["id"]), key=lambda s: s["time"])
    if previous and previous[-1]["fingerprint"] == fingerprint:
        saved = previous[-1]
        response = request(config, "HEAD", "data/" + saved["archive"])
        if response is not None:
            with response:
                present = int(response.headers.get("Content-Length", -1)) == saved["size"]
            if present:
                target.unlink()
                return {"snapshot_id": saved["id"], "data_added": 0, "unchanged": True, "archive": saved["archive"]}
    now = datetime.now(timezone.utc)
    identity = uuid.uuid4().hex + uuid.uuid4().hex
    name = app["id"] + "-" + now.strftime("%Y%m%dT%H%M%SZ").lower() + "-" + identity[:8] + ".tar.gz"
    digest = digest_file(target)
    size = target.stat().st_size
    with target.open("rb") as handle:
        response = request(config, "POST", "data/" + name, handle, size)
        with response:
            response.read()
    verify(config, "data/" + name, digest)
    saved = {"format": "vps-backup-archive-v1", "id": identity, "time": now.isoformat(),
             "hostname": config["server_id"], "app": app["id"], "tags": ["app:" + app["id"]],
             "archive": name, "sha256": digest, "fingerprint": fingerprint, "size": size}
    raw = json.dumps(saved).encode()
    response = request(config, "POST", "snapshots/" + identity + ".json", raw, len(raw))
    with response:
        response.read()
    verify(config, "snapshots/" + identity + ".json", hashlib.sha256(raw).hexdigest())
    target.unlink()
    return {"snapshot_id": identity, "data_added": size, "unchanged": False, "archive": name}
