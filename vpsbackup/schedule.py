import math
import os
from pathlib import Path
import shlex

from .common import BackupError, absolute, atomic_text, execute


UNIT = "vps-backup-client"
MARKER = "# Managed by vps-backup schedule\n"
UNIT_DIRECTORY = Path("/etc/systemd/system")


def unit_argument(value):
    # systemd performs its own percent and dollar expansion, even inside quotes.
    value = str(value)
    if any(c in value for c in "\n\r\x00"):
        raise BackupError("Invalid unit argument")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$") + '"'


def configure(config_path, mode, calendar, min_age_hours, command=None):
    if os.name != "posix" or os.geteuid() != 0:
        raise BackupError("Schedule management requires root on a systemd VPS")
    if not math.isfinite(min_age_hours) or min_age_hours < 0:
        raise BackupError("Minimum age must be a finite number >= 0")
    config_path = absolute(str(config_path), "Client config")
    command = absolute(command or "/usr/local/bin/vps-backup", "Installed command")
    if not command.is_file() or not os.access(command, os.X_OK):
        raise BackupError("Use --command with the installed executable's absolute path")
    if not calendar or any(c in calendar for c in "\n\r\x00%"):
        raise BackupError("Invalid systemd calendar")
    execute(["systemd-analyze", "calendar", calendar], timeout=30)
    args = [str(command), "--config", str(config_path), "run", "--min-age-hours", str(min_age_hours)]
    folder = UNIT_DIRECTORY
    service = folder / (UNIT + ".service")
    timer = folder / (UNIT + ".timer")
    for path in (service, timer):
        if path.is_symlink() or (path.exists() and not path.read_text().startswith(MARKER)):
            raise BackupError("Refusing to replace an unmanaged unit: " + str(path))
    enabled = mode in ("systemd", "both")
    if enabled:
        atomic_text(service, MARKER + "[Unit]\nDescription=VPS restic client backup\n"
                    "Wants=network-online.target\nAfter=network-online.target\n\n[Service]\n"
                    "Type=oneshot\nExecStart=" + " ".join(unit_argument(a) for a in args) +
                    "\nUMask=0077\nNice=10\nIOSchedulingClass=best-effort\nIOSchedulingPriority=7\n"
                    "TimeoutStartSec=5h\nTimeoutStopSec=90\n")
        atomic_text(timer, MARKER + "[Unit]\nDescription=VPS backup schedule\n\n[Timer]\n"
                    "OnCalendar=" + calendar + "\nRandomizedDelaySec=5m\nPersistent=true\n\n"
                    "[Install]\nWantedBy=timers.target\n")
        service.chmod(0o644)
        timer.chmod(0o644)
        execute(["systemctl", "daemon-reload"], timeout=30)
        execute(["systemctl", "enable", "--now", UNIT + ".timer"], timeout=30)
        # Restart so a calendar change takes effect on an already active timer.
        execute(["systemctl", "restart", UNIT + ".timer"], timeout=30)
    elif timer.exists():
        execute(["systemctl", "disable", "--now", UNIT + ".timer"], timeout=30)
    return {"status": "success", "operation": "schedule", "mode": mode,
            "timer_enabled": enabled, "unit": UNIT + ".timer", "calendar": calendar,
            "min_age_hours": min_age_hours, "n8n_command": shlex.join(args),
            "note": "n8n schedules are managed in n8n; this command only manages the local timer"}
