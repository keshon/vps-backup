"""Terminal help, next-step diagnostics and summaries."""
import argparse
from datetime import datetime
import difflib
from pathlib import Path


GROUPS = (
    ("Getting started", (("setup", "Connect this VPS using its recovery key; safe to retry"),
                         ("preflight", "Check local folders and free space; does not contact the cloud"))),
    ("Everyday use", (("backup", "Save a backup now"), ("status", "Show the last backup result; does not verify stored data"),
                      ("list", "Show saved backups"), ("export", "Download a backup as a normal .tar.gz"),
                      ("verify", "Read and check stored backup metadata (or --read-data for all data)"),
                      ("apps", "Show or change the folders included in backups"))),
    ("Scheduling and storage", (("schedule", "Use systemd, n8n, both, or manual runs"),
                                ("storage", "Choose encrypted restic snapshots or ordinary archives"))),
    ("Backup-server administration", (("setup-server", "Configure the shared cloud gateway"),
                                     ("enroll", "Create a recovery key for another VPS"),
                                     ("cloud-space", "Show the cloud pool's available space"))),
)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        if "--bundle" in message:
            self.exit(2, "error: --bundle needs the path to an existing recovery key.\n\n"
                      "The backup server creates this file with vps-backup enroll. Copy it here, then run:\n"
                      "  vps-backup setup --bundle /root/my-vps.bundle.json\n\n"
                      "Or run vps-backup setup and let it ask for the path.\n")
        if "argument COMMAND: invalid choice:" in message:
            command = message.split("invalid choice: ", 1)[1].split("'", 2)[1]
            names = [name for _, group in GROUPS for name, _ in group]
            matches = difflib.get_close_matches(command, names, n=1)
            suggestion = "Did you mean: vps-backup " + matches[0] + "?\n" if matches else ""
            self.exit(2, "error: Unknown command: " + command + "\n\n" + suggestion
                      + "help: Run vps-backup --help to see commands grouped by task.\n")
        self.exit(2, "error: " + message + "\n\n"
                  "help: Run " + self.prog + " --help for examples and available options.\n")

    def format_help(self):
        if self.prog != "vps-backup":
            return super().format_help()
        lines = ["VPS Backup — Docker app backups with a shared cloud gateway", "", "Usage: vps-backup COMMAND [options]", ""]
        for heading, commands in GROUPS:
            lines.append(heading)
            lines += ["  " + name.ljust(16) + description for name, description in commands]
            lines.append("")
        lines += ["Start here", "  vps-backup setup", "  vps-backup setup --bundle /root/my-vps.bundle.json", "",
                  "Options", "  --config PATH   Use another configuration file (before or after the command)",
                  "  --json          Return JSON for scripts; non-terminal output stays JSON by default",
                  "  --version       Show the installed version", "",
                  "Use vps-backup COMMAND --help for command examples.",
                  "Older names (run, snapshots, doctor, check, setup-client) still work.",
                  "Advanced commands: init (repository only), restore (raw restic paths), discover, audit-gateway, gateway.", ""]
        return "\n".join(lines)


def welcome(config_path, configured):
    if not configured:
        return ("VPS Backup is installed. This machine still needs setup.\n\n"
                "  vps-backup setup\n\n"
                "Setup asks for the .bundle.json recovery key created on your backup server,\n"
                "then lets you choose folders. It creates " + str(config_path) + " for you.\n"
                "You don't need to create that file yourself.\n\nUse vps-backup --help to see commands.")
    return ("VPS Backup\nConfiguration: " + str(config_path) + "\n\n"
            "  vps-backup status       What happened on the last run?\n"
            "  vps-backup backup       Back up now\n"
            "  vps-backup apps         View or change the app list\n"
            "  vps-backup --help       All commands, grouped by task")


def error_text(message, config_path):
    lines = str(message).splitlines()
    text = "error: " + (lines[0] if lines else "The command could not complete")
    if len(lines) > 1:
        text += "\n\n" + "\n".join(lines[1:])
    lower = str(message).lower()
    if "another operation is running" in lower:
        text += "\n\nhelp: A backup or setup already holds the lock. Wait for it to finish, then retry.\n"
        text += "      Do not remove the lock file; that would allow overlapping operations."
    elif "insufficient free space" in lower:
        text += "\n\nhelp: Run vps-backup preflight to see the scratch-space requirement.\n"
        text += "      Free space on this VPS; deleting cloud backups will not free local disk space."
    elif "configuration file" in lower or "cannot find the configuration" in lower:
        if Path(config_path).name == "server.json":
            text += "\n\nhelp: The shared backup gateway needs configuration first.\n  vps-backup setup-server --help"
        else:
            text += "\n\nhelp: This VPS needs setup. You don't need to create client.json yourself.\n"
            text += "  vps-backup setup\n  vps-backup setup --bundle /path/to/my-vps.bundle.json"
    elif "missing repository_password" in lower or "invalid config version" in lower:
        text += "\n\nhelp: Use the original recovery key generated by vps-backup enroll on the backup server."
    elif "configure at least one app" in lower:
        text += "\n\nhelp: The recovery key is present, but no folders have been chosen.\n  vps-backup setup"
    elif "restic failed" in lower:
        text += "\n\nhelp: Read the private diagnostics on this VPS, then check access to the backup server.\n"
        text += "      The log is under state_dir/last-error.log in " + str(config_path) + "."
    elif "cannot run restic" in lower or "install restic" in lower:
        text += "\n\nhelp: On Debian/Ubuntu, install the backup engine, then retry:\n  sudo apt install restic"
    return text


def human_result(result):
    operation = result.get("operation")
    if operation == "setup-client":
        heading = "Ready to back up." if result.get("ready") else "Folders saved; connection setup is still pending."
        return (heading + "\nConfiguration: " + result["config"] + "\nApps: " + str(result["apps"])
                + "\n\nNext:\n  " + result["next"])
    if operation == "doctor":
        return ("Ready locally: " + str(len(result["apps"])) + " app(s).\n"
                "Free: " + size(result["free_bytes"]) + "; required for temporary copies: " + size(result["required_free_bytes"])
                + "\nThis did not contact the cloud. To back up now:\n  vps-backup backup")
    if operation == "status":
        if result.get("never_run"):
            return "No backup has run on this VPS yet.\n\nNext:\n  vps-backup backup"
        return backup_result(result["last_run"], "Last backup") + "\n\nThis reports the last run; use vps-backup verify to check stored data."
    if operation == "apps":
        rows = ["Apps included in backups:"]
        for app in result["apps"]:
            method = "stop: " + ", ".join(app["stop_containers"]) if app.get("stop_containers") else "SQLite online copy" if app.get("sqlite") else "files"
            rows.append("  " + app["id"] + "  " + app["path"] + "  (" + method + ")")
        return "\n".join(rows) + "\n\nChange the selection:\n  vps-backup apps --select"
    if operation == "export":
        return "Saved " + result["archive"] + "\nYou can unpack this .tar.gz with tar or 7-Zip."
    if operation == "schedule":
        text = "Local timer: " + ("enabled · " + result["calendar"] if result["timer_enabled"] else "disabled")
        return text + "\n\nn8n SSH command:\n  " + result["n8n_command"] + "\n\n" + result["note"]
    if operation == "enroll":
        return ("Recovery key created: " + result["bundle"] + "\n\n"
                "Copy this file to the target VPS, then run there:\n"
                "  vps-backup setup --bundle /path/to/" + Path(result["bundle"]).name + "\n"
                "Keep a private copy off the backup server. It is needed for recovery.")
    if operation == "cloud-space":
        return "Cloud pool\n" + "\n".join("  " + label + ": " + size(result.get(field + "_bytes")) for field, label in (("free", "Free"), ("used", "Used"), ("total", "Total")))
    if operation == "check":
        return "Stored backup verification passed."
    if operation == "init":
        return "The cloud repository is ready.\nNext:\n  vps-backup backup"
    if operation == "storage":
        return "Storage format: " + result["storage_mode"] + ". Existing backups and schedules were kept."
    if "stage" in result:
        return backup_result(result, "Backup")
    return "Completed: " + str(operation or "command")


def size(value):
    if value is None:
        return "not reported"
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            return f"{amount:.1f} {unit}"
        amount /= 1024


def backup_result(result, label):
    if result.get("skipped"):
        return label + " skipped: a recent backup already succeeded. No new snapshot was created."
    text = label + ": " + result.get("status", "unknown")
    if result.get("completed_at"):
        text += " · " + datetime.fromtimestamp(result["completed_at"]).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    for app in result.get("apps", []):
        text += "\n  " + app["id"] + ": " + app["status"]
        if app.get("snapshot_id"):
            text += " · " + app["snapshot_id"][:8]
    if result.get("error"):
        text += "\n" + result["error"]
    return text
