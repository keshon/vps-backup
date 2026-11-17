import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import signal
import sys
import tempfile

from . import __version__, client, server, schedule, recovery, archive, onboarding, presentation
from .common import BackupError, absolute, atomic_json, locked, overlaps, private_json


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
    cli = presentation.Parser(prog="vps-backup", description="Back up Docker apps to your shared cloud gateway")
    cli.add_argument("--version", action="version", version=__version__)
    cli.add_argument("--config", type=Path, help="Another configuration file; defaults depend on the command")
    cli.add_argument("--json", action="store_true", help="Return machine-readable JSON")
    cli.add_argument("--bundle", type=Path, help="Use a recovery key file for snapshots/export on any computer")
    cli.add_argument("--local-repository", type=Path, help="Read a downloaded restic repository instead of the gateway")
    commands = cli.add_subparsers(dest="action", required=True, metavar="COMMAND")
    commands.add_parser("preflight", aliases=["doctor"], help="Check local folders and free space").set_defaults(action="doctor")
    run = commands.add_parser("backup", aliases=["run"], help="Save a backup now")
    run.add_argument("--min-age-hours", type=float, default=0,
                     help="Skip if this backup scope succeeded within this many hours")
    run.set_defaults(action="run")
    run.add_argument("--force", action="store_true", help="Ignore the recent-success guard")
    scheduling = commands.add_parser("schedule", help="Configure local scheduling and print the n8n SSH command")
    scheduling.add_argument("--mode", choices=("manual", "n8n", "systemd", "both"), required=True)
    scheduling.add_argument("--calendar", default="*-*-* 06:00:00", help="systemd OnCalendar expression")
    scheduling.add_argument("--min-age-hours", type=float, default=20)
    scheduling.add_argument("--command", help="Absolute path to the installed client command")
    commands.add_parser("init", help="Initialize this client's remote repository once")
    snapshots = commands.add_parser("list", aliases=["snapshots"], help="Show saved backups by date and app")
    snapshots.set_defaults(action="snapshots")
    snapshots.add_argument("--mode", choices=("restic", "archive"))

    exporting = commands.add_parser("export", help="Save a normal .tar.gz archive from a backup")
    exporting.add_argument("--snapshot", default="latest")
    exporting.add_argument("--app")
    exporting.add_argument("--to", type=Path)
    exporting.add_argument("--mode", choices=("restic", "archive"))

    storage = commands.add_parser("storage", help="Choose the backup storage format")
    storage.add_argument("mode", choices=("restic", "archive"))
    commands.add_parser("status", help="Show the last backup result without contacting the cloud")
    commands.add_parser("logs", help="Show the last failure with its actual diagnostic message")
    apps = commands.add_parser("apps", help="Show or change included folders")
    apps.add_argument("--select", action="store_true", help="Choose folders again; keeps the old selection unless you save")
    apps.add_argument("--base", type=Path, default=Path("/opt/dockers"))
    check = commands.add_parser("verify", aliases=["check"], help="Check stored backup integrity; unlike status, this contacts the cloud")
    check.set_defaults(action="check")
    check.add_argument("--read-data", action="store_true", help="Download and verify every stored data pack")
    recovering = commands.add_parser("restore", help="Restore and verify into a separate empty directory")
    recovering.add_argument("--snapshot", required=True)
    recovering.add_argument("--to", type=Path, required=True)
    discovery = commands.add_parser("discover", help="List candidate apps and SQLite databases; no changes")
    discovery.add_argument("--base", type=Path, default=Path("/opt/dockers"))
    setup = commands.add_parser("setup", aliases=["setup-client"], help="Connect this VPS with a recovery key; safe to retry")
    setup.set_defaults(action="setup-client")
    setup.add_argument("--bundle", type=Path, default=argparse.SUPPRESS, help="Existing .bundle.json recovery key, created by enroll on the backup server")
    setup.add_argument("--base", type=Path, default=Path("/opt/dockers"))
    setup.add_argument("--apps-file", type=Path, help="Private JSON app array, for unattended setup")
    setup.add_argument("--storage-mode", choices=("restic", "archive"), help="Default: keep the existing mode, or restic for a new client")
    setup.add_argument("--initialize", action="store_true", default=True, help=argparse.SUPPRESS)
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
    for child in set(commands.choices.values()):
        child.add_argument("--config", type=Path, default=argparse.SUPPRESS, help="Use another configuration file")
        child.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Return machine-readable JSON")
    setup.epilog = "Example: vps-backup setup --bundle /root/my-vps.bundle.json. Omit --bundle to be prompted. Safe to retry after a connection failure."
    run.epilog = "Example: vps-backup backup. Existing n8n calls using run remain supported."
    check.epilog = "Example: vps-backup verify --read-data. This downloads all stored data; status only reports the last run."
    return cli


def main(argv=None):
    os.umask(0o077)
    def interrupted(signum, frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, interrupted)
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        path = Path("/etc/vps-backup/client.json")
        configured = False
        try:
            if path.is_file():
                configured = bool(onboarding.read_config(path).get("apps"))
        except PermissionError:
            print("VPS Backup is installed. Its configuration is private to root.\n\nRun:\n  sudo vps-backup", file=sys.stderr)
            return 1
        except BackupError as exc:
            print(presentation.error_text(str(exc), path), file=sys.stderr)
            return 1
        print(presentation.welcome(path, configured))
        return 0
    args = parser().parse_args(arguments)
    if args.config is None:
        role = "server" if args.action in ("setup-server", "enroll", "gateway", "cloud-space") else "client"
        args.config = Path("/etc/vps-backup/" + role + ".json")
    human = sys.stdout.isatty() and not args.json
    workspace = None
    try:
        if (args.bundle and args.action != "setup-client" or args.local_repository) and args.action not in ("snapshots", "export"):
            raise BackupError("Recovery key and local-repository options apply only to snapshots/export")
        if args.action == "discover":
            result = {"status": "success", "apps": onboarding.discover(args.base)}
        elif args.action == "audit-gateway":
            result = server.audit_gateway(args.rclone_binary)
        elif args.action == "setup-server":
            result = server.setup(args.config, args.remote, args.rclone_config, args.public_url,
                                  args.listen, args.tls_cert, args.tls_key, args.rclone_binary, args.trusted_proxy)
        elif args.action == "setup-client":
            result = onboarding.setup_client(args.bundle, args.config, args.base, args.apps_file, args.initialize, args.storage_mode)
        elif args.action in ("enroll", "gateway", "cloud-space"):
            config = onboarding.read_config(args.config)
            if args.action == "enroll":
                result = server.enroll(config, args.id)
            elif args.action == "cloud-space":
                result = server.cloud_space(config)
            else:
                server.serve(config)
        else:
            if args.action == "init" and not args.config.exists():
                raise BackupError("There is no client configuration yet. init only prepares cloud storage.\n"
                                  "Start with: vps-backup setup\nSetup creates client.json from your recovery key.")
            if args.action in ("snapshots", "export", "logs"):
                config = onboarding.read_config(args.bundle or args.config, "recovery key" if args.bundle else "configuration")
                if args.bundle:
                    workspace = tempfile.TemporaryDirectory(prefix="vps-backup-recovery-")
                    config["state_dir"] = str(Path(workspace.name).resolve())
                    config["reserve_mib"] = 64
                client.validate_connection(config)
            else:
                config = client.validate(onboarding.read_config(args.config))
            if args.action == "schedule":
                result = schedule.configure(args.config, args.mode, args.calendar, args.min_age_hours, args.command)
            elif args.action == "doctor":
                result = {"status": "success", "operation": "doctor", **client.doctor(config)}
            elif args.action == "run":
                if human:
                    result = client.run(config, args.min_age_hours, args.force,
                                        progress=lambda message: print(message, file=sys.stderr, flush=True))
                else:
                    result = client.run(config, args.min_age_hours, args.force)
            elif args.action == "status":
                saved = Path(config["state_dir"]) / "last-run.json"
                if saved.exists():
                    previous = private_json(saved)
                    result = {"status": "success", "operation": "status", "last_run": previous} if human else previous
                else:
                    result = {"status": "success", "operation": "status", "never_run": True}
            elif args.action == "logs":
                log = Path(config["state_dir"]) / "last-error.log"
                if not log.is_file():
                    raise BackupError("No saved subprocess failure log exists at " + str(log) + ".\n"
                                      "Run vps-backup status for the last attempt. Older versions did not log timeouts or launch failures.")
                result = {"status": "success", "operation": "logs", "path": str(log),
                          "text": log.read_text(errors="replace")[-131072:]}
            elif args.action == "apps":
                if args.select:
                    onboarding.select_apps(config, args.config, args.base)
                result = {"status": "success", "operation": "apps", "apps": config["apps"]}
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
                        onboarding.initialize_repository(config)
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
                        if args.action == "init":
                            onboarding.initialize_repository(config)
                        else:
                            client.restic(config, args.action, *options)
                        result = {"status": "success", "operation": args.action}
    except KeyboardInterrupt:
        result = {"status": "failed", "error": "Interrupted; temporary files are cleaned on the next run if necessary"}
    except PermissionError:
        result = {"status": "failed", "error": "This command needs permission to read private configuration or write backup state.\n"
                  "On the VPS, run it with sudo. Your recovery key must remain private (chmod 600)."}
    except Exception as exc:
        result = {"status": "failed", "error": str(exc) if isinstance(exc, BackupError) else type(exc).__name__}
        if isinstance(exc, BackupError):
            guidance = presentation.error_text(str(exc), args.config)
            if "\n\n" in guidance:
                result["hint"] = guidance.split("\n\n", 1)[1]
    finally:
        if workspace is not None:
            workspace.cleanup()
    readable = human or (args.action in ("snapshots", "export", "logs") and not args.json)
    if readable:
        if result["status"] != "success":
            print(presentation.error_text(result.get("error", "The command could not complete"), args.config), file=sys.stderr)
        elif args.action == "snapshots":
            print(snapshot_table(result["snapshots"]), flush=True)
        else:
            print(presentation.human_result(result), flush=True)
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
