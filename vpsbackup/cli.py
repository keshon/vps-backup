import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import signal
import sys
import tempfile

from . import __version__, client, server, schedule, recovery, archive
from .common import BackupError, absolute, atomic_json, locked, overlaps, private_json


def discover(base):
    base = absolute(str(base), "Discovery directory")
    if not base.is_dir():
        raise BackupError("Discovery directory does not exist")
    apps = []
    for path in sorted(base.iterdir()):
        if not path.is_dir() or path.is_symlink() or path.name.startswith((".", "_")):
            continue
        databases = []
        unknown = []
        for folder, dirs, files in os.walk(path, followlinks=False):
            dirs[:] = [d for d in dirs if d not in (".git", ".venv", "node_modules")]
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
                     "unknown_databases": sorted(unknown)})
    return apps


def setup_client(bundle_path, config_path, base, apps_file=None, initialize=False, storage_mode="restic"):
    if config_path.exists():
        raise BackupError("Client config exists; refusing to overwrite")
    bundle = private_json(bundle_path)
    if apps_file:
        apps = private_json(apps_file)
    else:
        if not sys.stdin.isatty():
            raise BackupError("Setup needs a terminal, or --apps-file with an explicitly reviewed app list")
        apps = []
        for candidate in discover(base):
            print(json.dumps(candidate, indent=2), file=sys.stderr)
            if input("Back up this app? [y/N] ").strip().lower() != "y":
                continue
            unknown = candidate.pop("unknown_databases")
            if unknown:
                print("Non-SQLite databases require a consistent copy; enter exact containers to stop.", file=sys.stderr)
                names = input("Container names, comma-separated (blank: skip this app): ").strip()
                if not names:
                    continue
                candidate["stop_containers"] = [n.strip() for n in names.split(",") if n.strip()]
            apps.append(candidate)
    bundle["apps"] = apps
    bundle["storage_mode"] = storage_mode
    client.doctor(bundle)
    atomic_json(config_path, bundle)
    if initialize and storage_mode == "restic":
        state = Path(bundle["state_dir"])
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        with locked(state / "run.lock"):
            client.restic(bundle, "init")
    return {"status": "success", "operation": "setup-client", "config": str(config_path), "apps": len(apps)}


def restore(config, snapshot, target):
    if not re.fullmatch(r"[a-f0-9]{8,64}", snapshot):
        raise BackupError("Use an explicit snapshot ID from snapshots")
    target = absolute(str(target), "Restore destination")
    state = Path(config["state_dir"])
    sources = [Path(app["path"]) for app in config["apps"]] + [state]
    if any(overlaps(target, source) for source in sources):
        raise BackupError("Restore destination must not overlap sources or state")
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise BackupError("Restore destination must be empty")
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with locked(state / "run.lock"):
        client.restic(config, "restore", snapshot, "--target", str(target), "--verify")
    return {"status": "success", "operation": "restore", "to": str(target), "snapshot_id": snapshot}


def parser():
    cli = argparse.ArgumentParser(description="Restic clients with a central rclone gateway")
    cli.add_argument("--version", action="version", version=__version__)
    cli.add_argument("--config", type=Path, default=Path("/etc/vps-backup/client.json"))
    cli.add_argument("--bundle", type=Path, help="Use a recovery key file for snapshots/export on any computer")
    cli.add_argument("--local-repository", type=Path, help="Read a downloaded restic repository instead of the gateway")
    commands = cli.add_subparsers(dest="action", required=True)
    commands.add_parser("doctor", help="Offline source and scratch-space checks")
    run = commands.add_parser("run", help="Snapshot databases, back up apps, and check metadata")
    run.add_argument("--min-age-hours", type=float, default=0,
                     help="Skip if this backup scope succeeded within this many hours")
    run.add_argument("--force", action="store_true", help="Ignore the recent-success guard")
    scheduling = commands.add_parser("schedule", help="Configure local scheduling and print the n8n SSH command")
    scheduling.add_argument("--mode", choices=("manual", "n8n", "systemd", "both"), required=True)
    scheduling.add_argument("--calendar", default="*-*-* 06:00:00", help="systemd OnCalendar expression")
    scheduling.add_argument("--min-age-hours", type=float, default=20)
    scheduling.add_argument("--command", help="Absolute path to the installed client command")
    commands.add_parser("init", help="Initialize this client's remote repository once")
    snapshots = commands.add_parser("snapshots", help="List backups by date and app")
    snapshots.add_argument("--mode", choices=("restic", "archive"))
    snapshots.add_argument("--json", action="store_true", help="Machine-readable snapshot details")
    exporting = commands.add_parser("export", help="Save a normal .tar.gz archive from a backup")
    exporting.add_argument("--snapshot", default="latest")
    exporting.add_argument("--app")
    exporting.add_argument("--to", type=Path)
    exporting.add_argument("--mode", choices=("restic", "archive"))
    exporting.add_argument("--json", action="store_true")
    storage = commands.add_parser("storage", help="Choose the backup storage format")
    storage.add_argument("mode", choices=("restic", "archive"))
    commands.add_parser("status", help="Read the saved last-run result without cloud access")
    check = commands.add_parser("check", help="Check repository integrity")
    check.add_argument("--read-data", action="store_true", help="Download and verify every stored data pack")
    recovering = commands.add_parser("restore", help="Restore and verify into a separate empty directory")
    recovering.add_argument("--snapshot", required=True)
    recovering.add_argument("--to", type=Path, required=True)
    discovery = commands.add_parser("discover", help="List candidate apps and SQLite databases; no changes")
    discovery.add_argument("--base", type=Path, default=Path("/opt/dockers"))
    setup = commands.add_parser("setup-client", help="Import an enrollment bundle and confirm sources")
    setup.add_argument("--bundle", type=Path, required=True)
    setup.add_argument("--base", type=Path, default=Path("/opt/dockers"))
    setup.add_argument("--apps-file", type=Path)
    setup.add_argument("--storage-mode", choices=("restic", "archive"), default="restic")
    setup.add_argument("--initialize", action="store_true")
    gateway = commands.add_parser("setup-server", help="Audit gateway and create server configuration")
    gateway.add_argument("--remote", required=True)
    gateway.add_argument("--rclone-config", required=True)
    gateway.add_argument("--public-url", required=True)
    gateway.add_argument("--listen", default="127.0.0.1:8787")
    gateway.add_argument("--tls-cert")
    gateway.add_argument("--tls-key")
    gateway.add_argument("--trusted-proxy", action="store_true", help="Allow HTTP on a private IP behind a local HTTPS proxy")
    private_rclone = Path("/usr/local/lib/vps-backup/bin/rclone")
    gateway.add_argument("--rclone-binary", default=str(private_rclone) if private_rclone.exists() else None)
    audit = commands.add_parser("audit-gateway", help="Test installed rclone against disposable local storage")
    audit.add_argument("--rclone-binary", default=str(private_rclone) if private_rclone.exists() else None)
    commands.add_parser("gateway", help="Run authenticated append-only rclone gateway")
    commands.add_parser("cloud-space", help="Read cloud capacity using the backup server configuration")
    enroll = commands.add_parser("enroll", help="Create per-VPS authentication and recovery bundle")
    enroll.add_argument("--id", required=True)
    return cli


def main(argv=None):
    os.umask(0o077)
    def interrupted(signum, frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, interrupted)
    args = parser().parse_args(argv)
    workspace = None
    try:
        if (args.bundle and args.action != "setup-client" or args.local_repository) and args.action not in ("snapshots", "export"):
            raise BackupError("Recovery key and local-repository options apply only to snapshots/export")
        if args.action == "discover":
            result = {"status": "success", "apps": discover(args.base)}
        elif args.action == "audit-gateway":
            result = server.audit_gateway(args.rclone_binary)
        elif args.action == "setup-server":
            result = server.setup(args.config, args.remote, args.rclone_config, args.public_url,
                                  args.listen, args.tls_cert, args.tls_key, args.rclone_binary, args.trusted_proxy)
        elif args.action == "setup-client":
            result = setup_client(args.bundle, args.config, args.base, args.apps_file, args.initialize, args.storage_mode)
        elif args.action in ("enroll", "gateway", "cloud-space"):
            config = private_json(args.config)
            if args.action == "enroll":
                result = server.enroll(config, args.id)
            elif args.action == "cloud-space":
                result = server.cloud_space(config)
            else:
                server.serve(config)
        else:
            if args.action in ("snapshots", "export"):
                config = private_json(args.bundle or args.config)
                if args.bundle:
                    workspace = tempfile.TemporaryDirectory(prefix="vps-backup-recovery-")
                    config["state_dir"] = str(Path(workspace.name).resolve())
                    config["reserve_mib"] = 64
                client.validate_connection(config)
            else:
                config = client.validate(private_json(args.config))
            if args.action == "schedule":
                result = schedule.configure(args.config, args.mode, args.calendar, args.min_age_hours, args.command)
            elif args.action == "doctor":
                result = {"status": "success", "operation": "doctor", **client.doctor(config)}
            elif args.action == "run":
                result = client.run(config, args.min_age_hours, args.force)
            elif args.action == "status":
                result = private_json(Path(config["state_dir"]) / "last-run.json")
            elif args.action == "export":
                result = recovery.export(config, args.snapshot, args.app, args.to, args.mode,
                                         str(args.local_repository.resolve()) if args.local_repository else None)
            elif args.action == "snapshots":
                result = {"status": "success", "snapshots": recovery.snapshots(config, args.mode,
                          str(args.local_repository.resolve()) if args.local_repository else None)}
            elif args.action == "storage":
                state = Path(config["state_dir"])
                state.mkdir(parents=True, exist_ok=True, mode=0o700)
                with locked(state / "run.lock"):
                    if args.mode == "restic":
                        try:
                            client.restic(config, "snapshots", "--json")
                        except BackupError:
                            client.restic(config, "init")
                    config["storage_mode"] = args.mode
                    atomic_json(args.config, config)
                result = {"status": "success", "operation": "storage", "storage_mode": args.mode}
            elif args.action == "restore":
                if config.get("storage_mode") == "archive":
                    raise BackupError("Use export to download the archive, then unpack it into an empty folder")
                result = restore(config, args.snapshot, args.to)
            else:
                state = Path(config["state_dir"])
                state.mkdir(parents=True, exist_ok=True, mode=0o700)
                with locked(state / "run.lock"):
                    if config.get("storage_mode") == "archive":
                        saved = archive.snapshots(config)
                        if args.action == "check":
                            for item in saved:
                                archive.verify(config, "data/" + item["archive"], item["sha256"])
                        result = {"status": "success", "operation": args.action, "archives": len(saved)}
                    else:
                        options = ["--read-data"] if args.action == "check" and args.read_data else []
                        client.restic(config, args.action, *options)
                        result = {"status": "success", "operation": args.action}
    except KeyboardInterrupt:
        result = {"status": "failed", "error": "Interrupted; temporary files are cleaned on the next run if necessary"}
    except Exception as exc:
        result = {"status": "failed", "error": str(exc) if isinstance(exc, BackupError) else type(exc).__name__}
    finally:
        if workspace is not None:
            workspace.cleanup()
    if args.action == "export" and not args.json:
        if result["status"] == "success":
            print("Saved " + result["archive"], flush=True)
        else:
            print("Error: " + result.get("error", "Cannot export backup"), file=sys.stderr)
    elif args.action == "snapshots" and not args.json:
        if result["status"] == "success":
            print(snapshot_table(result["snapshots"]), flush=True)
        else:
            print("Error: " + result.get("error", "Cannot list backups"), file=sys.stderr)
    else:
        print(json.dumps(result), flush=True)
    return 0 if result["status"] == "success" else 1


def snapshot_table(snapshots, numbered=False):
    if not snapshots:
        return "No backups found."
    rows = [["SNAPSHOT", "DATE", "APP", "STORAGE", "SERVER"]]
    for saved in sorted(snapshots, key=lambda item: item.get("time", ""), reverse=True):
        try:
            date = datetime.fromisoformat(saved["time"].replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M %z")
        except (KeyError, ValueError):
            date = "unknown"
        apps = ",".join(tag[4:] for tag in saved.get("tags", []) if tag.startswith("app:")) or "unknown"
        rows.append([saved["id"][:8], date, apps, saved.get("mode", "archive" if saved.get("format") else "restic"), saved.get("hostname", "unknown")])
    if numbered:
        rows = [["#"] + rows[0]] + [[str(i)] + row for i, row in enumerate(rows[1:], 1)]
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    lines = ["  ".join(value.ljust(widths[i]) for i, value in enumerate(row)).rstrip() for row in rows]
    return "\n".join(lines)
