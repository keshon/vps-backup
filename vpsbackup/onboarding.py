"""Client enrollment, resumable setup, and app selection."""
import base64
import copy
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import ssl
import sys
import time
import urllib.error
import urllib.request

from . import archive, client
from .common import BackupError, absolute, atomic_json, execute, locked, private_json


def ask(prompt):
    print(prompt, end="", file=sys.stderr, flush=True)
    return input()


def discover(base):
    base = absolute(str(base), "App directory")
    if not base.is_dir():
        raise BackupError("App directory does not exist: " + str(base))
    apps = []
    for path in sorted(base.iterdir()):
        if not path.is_dir() or path.is_symlink() or path.name.startswith((".", "_")):
            continue
        databases = []
        unknown = []
        custom_store = False
        for folder, dirs, files in os.walk(path, followlinks=False):
            dirs[:] = [d for d in dirs if d not in (".git", ".venv", "node_modules")]
            if "wal.log" in files and any(name.startswith("snapshot-") and name.endswith(".json") for name in files):
                custom_store = True
            for name in files:
                if not name.endswith((".db", ".sqlite", ".sqlite3")):
                    continue
                file = Path(folder) / name
                if file.is_symlink():
                    continue
                with file.open("rb") as handle:
                    sqlite = handle.read(16) == b"SQLite format 3\x00"
                (databases if sqlite else unknown).append(file.relative_to(path).as_posix())
        name = re.sub(r"[^a-z0-9-]", "-", path.name.lower()).strip("-")[:63]
        apps.append({"id": name, "path": str(path), "sqlite": sorted(databases),
                     "unknown_databases": sorted(unknown), "custom_store": custom_store})
    return apps


def read_config(path, label="configuration"):
    path = Path(path).expanduser()
    if path.is_symlink():
        raise BackupError("The " + label + " is a symlink: " + str(path) + "\nUse the direct path to the original private file.")
    if not path.exists():
        raise BackupError("Cannot find the " + label + ": " + str(path))
    if not path.is_file():
        raise BackupError("The " + label + " path is not a file: " + str(path) + "\nChoose the .bundle.json file, not its parent directory.")
    if os.name == "posix" and path.is_file() and path.stat().st_mode & 0o077:
        raise BackupError("The " + label + " is readable by other users.\n"
                          "It contains passwords. Make it private, then retry:\n  chmod 600 " + shlex.quote(str(path)))
    try:
        result = private_json(path)
    except (ValueError, UnicodeError) as exc:
        if path.stat().st_size == 0:
            raise BackupError("The " + label + " is empty: " + str(path) + "\n"
                              "An empty file is not a recovery key. Keep it aside, then restart setup:\n"
                              "  mv -n -- " + shlex.quote(str(path)) + " " + shlex.quote(str(path) + ".empty") + "\n"
                              "  vps-backup setup\nSetup needs the original .bundle.json from the backup server.") from exc
        raise BackupError("The " + label + " is not valid JSON: " + str(path) + "\n"
                          "Use the original .bundle.json recovery key from the backup server.\n"
                          "An empty file or a filename you invent cannot replace that key.") from exc
    if not isinstance(result, dict):
        raise BackupError("Expected a JSON object in the " + label + ": " + str(path))
    return result


def recovery_key(given):
    if given:
        return Path(given).expanduser().resolve()
    candidates = sorted(set(Path.cwd().glob("*.bundle.json")) | set(Path.home().glob("*.bundle.json")))
    if len(candidates) == 1:
        print("Found recovery key: " + str(candidates[0]), file=sys.stderr)
        return candidates[0]
    if not sys.stdin.isatty():
        raise BackupError("Setup needs this VPS's recovery key.\n"
                          "On the backup server, run: vps-backup enroll --id YOUR_VPS_NAME\n"
                          "Copy the resulting .bundle.json file here, then run:\n"
                          "  vps-backup setup --bundle /path/to/YOUR_VPS_NAME.bundle.json\n"
                          "The key is an existing input file; setup creates client.json for you.")
    print("\nConnect this VPS to your backup server.\n"
          "You need the .bundle.json recovery key created there by:\n"
          "  vps-backup enroll --id YOUR_VPS_NAME\n"
          "Copy that file to this machine first. Do not create an empty client.json.\n", file=sys.stderr)
    text = ask("Recovery key path (Enter to leave setup for later): ").strip().strip('"').strip("'")
    if not text:
        raise BackupError("Setup paused. Your installed files are fine.\n"
                          "When the recovery key is here, run: vps-backup setup")
    return Path(text).expanduser().resolve()


def container_mounts():
    if not shutil.which("docker"):
        return []
    try:
        ids = execute(["docker", "ps", "-aq"], timeout=10).decode().split()
        if not ids:
            return []
        containers = json.loads(execute(["docker", "inspect", *ids], timeout=15))
    except (BackupError, ValueError):
        print("Docker mounts could not be read. Use docker ps to find container names if an app needs a stop.", file=sys.stderr)
        return []
    return [(item["Name"].lstrip("/"), Path(mount["Source"]))
            for item in containers for mount in item.get("Mounts", [])
            if mount.get("Type") == "bind" and mount.get("RW") and mount.get("Source")]


def choose_apps(base):
    if not sys.stdin.isatty():
        raise BackupError("App selection needs a terminal.\n"
                          "Run vps-backup setup in your SSH terminal, or provide --apps-file /path/to/apps.json.")
    candidates = discover(base)
    if not candidates:
        raise BackupError("No app folders found under " + str(base) + ".\n"
                          "Choose another parent directory: vps-backup setup --base /path/to/apps")
    print("\nChoose the app folders to back up. Nothing is stopped during setup.", file=sys.stderr)
    mounts = container_mounts()
    apps = []
    for candidate in candidates:
        print("\n" + candidate["id"] + "  (" + candidate["path"] + ")", file=sys.stderr)
        for database in candidate["sqlite"]:
            print("  SQLite: " + database + " — copied consistently while running", file=sys.stderr)
        unknown = candidate.pop("unknown_databases")
        custom_store = candidate.pop("custom_store")
        root = Path(candidate["path"])
        containers = sorted({name for name, source in mounts if source == root or root in source.parents})
        if containers:
            print("  Containers writing here: " + ", ".join(containers), file=sys.stderr)
        if unknown:
            print("  Other database files: " + ", ".join(unknown), file=sys.stderr)
        if custom_store:
            print("  WAL/snapshot files detected: stop the writer while copying these files.", file=sys.stderr)
        if ask("Include this app? [y/N] ").strip().lower() not in ("y", "yes"):
            continue
        print("  Does this app need to stop for a consistent copy?\n"
              "  Use container names for non-SQLite databases or custom WAL/snapshot stores.", file=sys.stderr)
        suggested = ",".join(containers) if unknown or custom_store else ""
        prompt = "Containers to stop briefly [" + suggested + "; Enter accepts, '-' skips this app]: " if suggested else "Containers to stop briefly, comma-separated (Enter: keep running): "
        text = ask(prompt).strip() or suggested
        if text == "-":
            continue
        if text:
            candidate["stop_containers"] = [name.strip() for name in text.split(",") if name.strip()]
        elif unknown or custom_store:
            print("  Skipped: those database files need a stop method before they can be backed up safely.", file=sys.stderr)
            continue
        apps.append(candidate)
    if not apps:
        raise BackupError("No apps selected; nothing was saved.\nRun vps-backup setup when you are ready to choose them.")
    print("\nSelected: " + ", ".join(app["id"] for app in apps), file=sys.stderr)
    if ask("Save this selection? [y/N] ").strip().lower() not in ("y", "yes"):
        raise BackupError("Selection cancelled. Your existing configuration was not changed.")
    return apps


def repository_ready(config):
    """Distinguish a missing repository from an unreachable or unauthorized one."""
    url = config["repository"].removeprefix("rest:") + "config"
    auth = (config["rest_username"] + ":" + config["rest_password"]).encode()
    request = urllib.request.Request(url, method="HEAD", headers={
        "Authorization": "Basic " + base64.b64encode(auth).decode(),
        "Accept": "application/vnd.x.restic.rest.v2",
    })
    try:
        with urllib.request.urlopen(request, timeout=30, context=ssl.create_default_context(cafile=config.get("ca_file"))):
            return True
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        if code == 404:
            return False
        if code in (401, 403):
            raise BackupError("The backup server rejected this recovery key.\n"
                              "Use the original key for this VPS; do not regenerate it for existing backups.") from None
        raise BackupError("The backup server returned HTTP " + str(code) + ".\n"
                          "Your setup is saved. Check the gateway, then retry: vps-backup setup") from None
    except (OSError, urllib.error.URLError):
        raise BackupError("Cannot reach the backup server over HTTPS.\n"
                          "Your setup is saved. Check the gateway address, DNS and HTTPS routing, then retry:\n"
                          "  vps-backup setup") from None


def initialize_repository(config):
    if config.get("storage_mode", "restic") == "restic":
        if not repository_ready(config):
            client.restic(config, "init")
        else:
            # Verify the saved password opens it; never replace an existing repository.
            client.restic(config, "snapshots", "--json")
    else:
        archive.snapshots(config)


def setup_client(bundle_path, config_path, base, apps_file=None, initialize=True, storage_mode=None):
    config_path = absolute(str(Path(config_path).expanduser()), "Configuration path")
    existing = read_config(config_path) if config_path.exists() else None
    if existing:
        config = copy.deepcopy(existing)
        if bundle_path and Path(bundle_path).expanduser().resolve() != config_path:
            supplied = read_config(recovery_key(bundle_path), "recovery key")
            keys = ("repository", "repository_password", "rest_username", "rest_password", "server_id")
            if any(config.get(key) != supplied.get(key) for key in keys):
                raise BackupError("This machine already has a different recovery key configured.\n"
                                  "Nothing was replaced. Use a separate --config path for a different VPS.")
    else:
        config = read_config(recovery_key(bundle_path), "recovery key")
    client.validate_connection(config)
    config["storage_mode"] = storage_mode or config.get("storage_mode", "restic")
    if apps_file:
        apps = private_json(Path(apps_file).expanduser())
        if not isinstance(apps, list):
            raise BackupError("The apps file must contain a JSON array of app definitions.")
        config["apps"] = apps
    elif not config.get("apps"):
        config["apps"] = choose_apps(base)
    client.doctor(config)
    state = Path(config["state_dir"])
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with locked(state / "run.lock"):
        # Don't overwrite another setup that completed while this wizard was open.
        current = read_config(config_path) if config_path.exists() else None
        if current != existing:
            raise BackupError("Configuration changed during setup. Nothing was replaced; run vps-backup setup again.")
        if existing != config:
            if existing is not None:
                backup = config_path.with_name(config_path.name + ".before-setup-" + str(time.time_ns()))
                atomic_json(backup, existing)
            atomic_json(config_path, config)
        if initialize:
            initialize_repository(config)
    return {"status": "success", "operation": "setup-client", "config": str(config_path),
            "apps": len(config["apps"]), "ready": initialize,
            "next": "vps-backup backup" if initialize else "vps-backup setup"}


def select_apps(config, config_path, base):
    selection = choose_apps(base)
    changed = copy.deepcopy(config)
    changed["apps"] = selection
    client.doctor(changed)
    state = Path(config["state_dir"])
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with locked(state / "run.lock"):
        if read_config(config_path) != config:
            raise BackupError("Configuration changed while you were choosing apps. Run vps-backup apps --select again.")
        backup = config_path.with_name(config_path.name + ".before-apps-" + str(time.time_ns()))
        atomic_json(backup, config)
        atomic_json(config_path, changed)
    config.update(changed)
