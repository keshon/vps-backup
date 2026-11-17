#!/usr/bin/env python3
"""Interactive recovery on a personal Windows or Linux computer."""
import argparse
import hashlib
import io
import os
from pathlib import Path
import platform
import shutil
import sys
import tempfile
import urllib.request
import zipfile

from vpsbackup import client, recovery
from vpsbackup.cli import snapshot_table
from vpsbackup.common import BackupError, private_json


def engine():
    found = shutil.which("restic")
    if found:
        return found
    if os.name != "nt":
        raise BackupError("Install restic first: sudo apt install restic (Debian/Ubuntu)")
    version = "0.19.1"
    architecture = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "amd64"
    folder = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".cache"))) / "vps-backup" / "restic" / version
    binary = folder / "restic.exe"
    if binary.is_file():
        return str(binary)
    print("Downloading the recovery engine from the official restic release (once)...", flush=True)
    base = "https://github.com/restic/restic/releases/download/v" + version + "/"
    name = "restic_" + version + "_windows_" + architecture + ".zip"
    def download(url):
        request = urllib.request.Request(url, headers={"User-Agent": "vps-backup-recovery"})
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.read()
    checksums = download(base + "SHA256SUMS").decode()
    expected = next((line.split()[0] for line in checksums.splitlines()
                     if line.split() and line.split()[-1].lstrip("*") == name), None)
    data = download(base + name)
    if not expected or hashlib.sha256(data).hexdigest() != expected:
        raise BackupError("Recovery engine download failed checksum verification")
    folder.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        names = [n for n in package.namelist() if n.endswith(".exe")]
        if len(names) != 1:
            raise BackupError("Unexpected recovery engine package")
        binary.write_bytes(package.read(names[0]))
    return str(binary)


def key_file(given):
    if given:
        return given.expanduser().resolve()
    candidates = sorted(set(Path.cwd().glob("*.bundle.json")) |
                        set((Path.home() / ".ssh/vps-backup-recovery").glob("*.bundle.json")))
    if len(candidates) == 1:
        print("Recovery key: " + str(candidates[0]))
        return candidates[0]
    if candidates:
        for index, path in enumerate(candidates, 1):
            print(str(index) + ". " + path.name)
        choice = input("Recovery key number [1]: ").strip() or "1"
        try:
            number = int(choice)
            if number < 1:
                raise ValueError()
            return candidates[number - 1]
        except (ValueError, IndexError):
            raise BackupError("Choose a number from the list") from None
    text = input("Recovery key file (you can drag it here): ").strip().strip('"').strip("'")
    return Path(text).expanduser().resolve()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Choose a backup and save a normal .tar.gz archive")
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--repository", type=Path, help="Downloaded restic repository, for recovery without the server")
    parser.add_argument("--list", action="store_true", help="List backups without prompting")
    parser.add_argument("--app")
    parser.add_argument("--snapshot", default="latest")
    parser.add_argument("--mode", choices=("restic", "archive"))
    parser.add_argument("--to", type=Path)
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        config = private_json(key_file(args.bundle))
        with tempfile.TemporaryDirectory(prefix="vps-backup-recovery-") as temp:
            config["state_dir"] = str(Path(temp).resolve())
            config["reserve_mib"] = 64
            client.validate_connection(config)
            repository = str(args.repository.resolve()) if args.repository else None
            items = []
            errors = []
            modes = [args.mode] if args.mode else (["restic"] if repository else ["restic", "archive"])
            for mode in modes:
                try:
                    if mode == "restic":
                        config["restic_binary"] = engine()
                    items += [dict(s, mode=mode) for s in recovery.snapshots(config, mode, repository)]
                except Exception as exc:
                    errors.append(mode + ": " + (str(exc) if isinstance(exc, BackupError) else type(exc).__name__))
            if not items:
                raise BackupError("No backups available. " + "; ".join(errors))
            items.sort(key=lambda s: s["time"], reverse=True)
            print(snapshot_table(items, numbered=not args.list))
            if errors:
                print("Some backups could not be listed: " + "; ".join(errors), file=sys.stderr)
            if args.list:
                return 0
            if args.to:
                selected = recovery.select(items, args.snapshot, args.app)
                target = args.to.expanduser().resolve()
            else:
                text = input("Backup number [1, newest]: ").strip() or "1"
                try:
                    number = int(text)
                    if number < 1:
                        raise ValueError()
                    selected = items[number - 1]
                except (ValueError, IndexError):
                    raise BackupError("Choose a number from the list") from None
                app = next(t[4:] for t in selected["tags"] if t.startswith("app:"))
                folder = Path.home() / "Downloads"
                if not folder.is_dir():
                    folder = Path.cwd()
                default = folder / (app + "-" + selected["time"][:10] + "-" + selected["id"][:8] + ".tar.gz")
                text = input("Save as [" + str(default) + "]: ").strip().strip('"')
                target = Path(text).expanduser().resolve() if text else default
            print("Saving archive...", flush=True)
            result = recovery.export(config, selected["id"], target=target, mode=selected["mode"], repository=repository)
            print("Saved " + result["archive"])
            print("Unpack it with 7-Zip, tar, or your archive manager. The archive contains decrypted app files.")
        return 0
    except (KeyboardInterrupt, EOFError):
        print("Cancelled.", file=sys.stderr)
        return 1
    except Exception as exc:
        print("Error: " + (str(exc) if isinstance(exc, BackupError) else type(exc).__name__), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
