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
sudo vps-backup --config /etc/vps-backup/server.json enroll --id my-vps
sudo scp /etc/vps-backup/clients/my-vps.bundle.json root@TARGET:/root/
```

Keep a secure copy of that bundle off the backup server. It contains the
encryption password needed to recover the backups.

On the target VPS, download the tool and install it:

```bash
git clone https://github.com/keshon/vps-backup.git
cd vps-backup
sudo bash install.sh --bundle /root/my-vps.bundle.json
```

The installer asks which folders under `/opt/dockers` to include. Review the
choices. For an app that needs a consistent copy while stopped, name every
container that writes to its files. The installer initializes the repository
and leaves scheduling off.

Take the first backup and check that it appeared:

```bash
sudo vps-backup run
sudo vps-backup snapshots
```

[Test a restore](recovery.md#test-a-snapshot) before relying on it.
Then choose one of the following schedules.

## Choose the storage format

The default is `restic`: encrypted snapshots that upload changed chunks.
For ordinary cloud archives instead, install with `--storage-mode archive` or
switch an existing client on the target VPS:

```bash
sudo vps-backup storage archive
sudo vps-backup run
sudo vps-backup snapshots
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
/usr/local/bin/vps-backup --config /etc/vps-backup/client.json run --min-age-hours 20
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
sudo vps-backup run --force
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
