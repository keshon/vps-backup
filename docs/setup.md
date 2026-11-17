# Set up a VPS

You need one backup server and a client on each VPS you want to back up.
The backup server can also run a client to back up its own folders.

## Prepare the backup server once

Follow [backup server setup](running.md#backup-server) to connect your cloud
storage and HTTPS address. Once that works, repeat the steps below for each VPS.
If you use Traefik in Docker, [install the HTTPS route](traefik.md) on that
server as part of setup. Target VPS do not need this route.

## Add another VPS

These commands assume a fresh Debian/Ubuntu VPS with git installed and the
default install paths. Replace `my-vps` with a unique name and `TARGET` with
its SSH address. For a nonstandard SSH port, add `-P PORT` to the scp command.

On the backup server, create this VPS's login and recovery key:

```bash
sudo vps-backup enroll --id my-vps
sudo scp /etc/vps-backup/clients/my-vps.bundle.json root@TARGET:/root/
```

Keep a secure copy of that bundle off the backup server. It contains the
encryption password needed to recover the backups. It is an existing input file;
you do not create it on the target or invent its contents.

On the target VPS, download the tool and install it:

```bash
git clone https://github.com/keshon/vps-backup.git
cd vps-backup
sudo bash install.sh --bundle /root/my-vps.bundle.json
```

You can also run `sudo bash install.sh` and enter the key's path when asked.
The installer asks which folders under `/opt/dockers` to include, suggests detected
container writers where available, and explains database-copy choices. Review
the selection before saving. It creates `/etc/vps-backup/client.json` and prepares
cloud storage. Existing schedules are kept; a new client starts without a timer.

Take the first backup and check that it appeared:

```bash
sudo vps-backup backup
sudo vps-backup list
```

[Test a restore](recovery.md#test-a-snapshot) before relying on it.
Then choose one of the following schedules.

## Already installed or stuck partway through?

Run this on the target VPS:

```bash
sudo vps-backup setup
```

Setup finds a recovery key in the current or home directory if exactly one is
present, or asks for its path. If client settings already exist, it resumes the
connection check without asking you to select apps again. It opens an existing
repository instead of trying to recreate it. A connection failure leaves your
settings saved so you can retry the same command.

If the installed command is an older version, update from your repository first:

```bash
git pull
sudo bash install.sh
```

Rerunning the installer updates this tool and resumes setup. It keeps the previous
runtime for rollback and preserves credentials, settings and schedules. It refuses
to overwrite a command belonging to another tool. `--install-only` updates commands
without starting the setup wizard.

`client.json` is setup's output. The `.bundle.json` recovery key is its input.
If you copied the original key directly to `client.json`, setup can complete it.
If you created an empty file, the error shows how to keep it aside and restart.
An unreadable or malformed nonempty file is never silently replaced.

You normally do not need `--config`. To deliberately use another output file,
pass `--config /absolute/path/client.json` to the installer or command. Use the
same path in later commands and schedules. It is accepted before or after the
command name.

## Change the app list

```bash
sudo vps-backup apps
sudo vps-backup apps --select
```

The second command lets you choose the list again. Cancelling keeps the current
configuration; saving keeps a private copy of it before writing the new list.
It does not delete older backups. The next scheduled backup uses the new list.

## Choose the storage format

The default is `restic`: encrypted snapshots that upload changed chunks.
For ordinary cloud archives instead, install with `--storage-mode archive` or
switch an existing client on the target VPS:

```bash
sudo vps-backup storage archive
sudo vps-backup backup
sudo vps-backup list
```

Archive mode creates one `.tar.gz` per app and uploads it only when file contents
or relevant metadata changed. It still builds the archive locally to compare it;
allow room for that archive as well as any consistent copies. Cloud archives are
readable without an encryption password.

Switch back with `sudo vps-backup storage restic`. Switching preserves old backups
and schedules. View older backups with `snapshots --mode restic` or
`snapshots --mode archive`; export either using the same `--mode` option.
[Local recovery](local-recovery.md) can list both modes together.

## Without n8n

On the target VPS, run backups Wednesdays at 05:00 Moscow time:

```bash
sudo vps-backup schedule --mode systemd \
  --calendar 'Wed *-*-* 05:00:00 Europe/Moscow' --min-age-hours 20
```

Check the next run or read the logs:

```bash
systemctl list-timers vps-backup-client.timer
journalctl -u vps-backup-client.service -n 50 --no-pager
```

Run the same `schedule` command with a different calendar to change the time.
The timer catches up after downtime and adds up to five minutes of random
delay, so the Wednesday run starts between 05:00 and 05:05.

## n8n only

On the target VPS, disable this tool's local timer and print its SSH command:

```bash
sudo vps-backup schedule --mode n8n --min-age-hours 20
```

Import [the workflow template](../deploy/n8n-backup.json) into n8n. Select the
VPS's SSH private-key credential in **Run backup**. Its account must be root,
or have permission to run the command with `sudo -n`.

In **Run backup**, use this command for the default installation:

```bash
/usr/local/bin/vps-backup --config /etc/vps-backup/client.json backup --json --min-age-hours 20
```

The template runs Wednesdays at 05:00, with the workflow timezone set to
`Europe/Moscow`. Change the Schedule Trigger or timezone if needed, then
publish it. **Validate backup result** checks the SSH exit code and JSON status.
A skipped recent backup counts as success. SSH or backup failures fail the
execution. [Optional Discord and Telegram notifications](n8n.md#discord-and-telegram)
report successful or skipped runs; both start disabled in the template.

The manual trigger uses the same guard. For an immediate additional backup,
use `run --force` on the VPS.

## n8n with a systemd fallback

Use the n8n workflow above for Wednesday at 05:00. On the target VPS, add a
fallback for Wednesday at 07:00 Moscow time:

```bash
sudo vps-backup schedule --mode both \
  --calendar 'Wed *-*-* 07:00:00 Europe/Moscow' --min-age-hours 20
```

If n8n completes the backup, the fallback sees that success and skips. If n8n
misses the run or the backup fails, systemd tries at 07:00. An overlapping
operation is rejected by the client lock. A backup still running at 07:00
therefore prevents that fallback attempt; the next attempt is the next trigger.

The 20-hour window starts after all apps and the metadata check succeed.
Skipped runs do not extend it. Changing the repository or app configuration
requires a new backup. The window measures elapsed time, not calendar weeks.

## Manual runs and switching modes

Force a backup now, even if a scheduled run just succeeded:

```bash
sudo vps-backup backup --force
```

Turn off the local timer:

```bash
sudo vps-backup schedule --mode manual
```

For fully manual operation, also unpublish the n8n workflow. `schedule` manages
only `vps-backup-client.timer`; it cannot edit n8n or disable older backup jobs.
Disable the old job after verifying its replacement.

The standard command reads `/etc/vps-backup/client.json` automatically.
You only need `--config` when deliberately using a different configuration.
[Configuration and troubleshooting](running.md) covers disk use, databases,
and failure recovery.
