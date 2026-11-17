# Configuration and troubleshooting

For routine installation, start with [setup examples](setup.md).

## Requirements

The installer supports Debian/Ubuntu, running as root. Clients need Python 3.10+
and restic for restic mode; a client configured to stop containers also needs Docker. The
backup server needs Python, rclone, and `htpasswd`. The installer uses OS
packages for Python/restic and downloads rclone 1.76.0 into the tool's private
directory, checking its archive against the release's HTTPS SHA256SUMS file.
It leaves the system rclone binary unchanged. That checksum checks the download
against the publisher's checksum; it is not an independently verified signature.

Before writes, the gateway's installed binary must pass the built-in audit.
Changing that binary invalidates the saved audit fingerprint. Ubuntu's tested
rclone 1.60.1-DEV failed the path traversal check; 1.76.0 passed the fixture audit.
This audit covers specific requests, not every possible server vulnerability.

## Backup server

The commands below create a new deployment. They do not migrate the old archive
runner or its schedules. Keep old backups until you have restored the new ones.

1. Install from this repository with `sudo bash install.sh --role server`.
2. Place the union configuration at `/etc/vps-backup/rclone.conf`, owned by root
   with mode `600`. The parent directory should be `700`.
3. Choose a new remote prefix and HTTPS address. Create the configuration:

```bash
sudo vps-backup --config /etc/vps-backup/server.json setup-server \
  --remote union-koofr:restic-vps \
  --rclone-config /etc/vps-backup/rclone.conf \
  --public-url https://backup.example.com/
```

This audits rclone using disposable local storage. It does not initialize cloud
repositories. The default listener is `127.0.0.1:8787`; configure your HTTPS
reverse proxy to reach it without buffering whole uploads. For direct TLS,
provide `--listen 0.0.0.0:8787 --tls-cert /path/to/fullchain.pem
--tls-key /path/to/privkey.pem`. Certificate paths must be readable inside the
service's filesystem restrictions. Do not use certificates under `/root`.

If Traefik runs in Docker and must reach a host service through the bridge
gateway, use its specific private IP with `--listen 172.19.0.1:8787
--trusted-proxy`. This explicitly permits HTTP on that private address; public
and wildcard HTTP listeners remain refused. Use this only behind a trusted
local HTTPS proxy, and restrict backend access to the intended bridge. The
connection between that proxy and the gateway is plaintext on the local
bridge. Targets still require HTTPS. Do not use this flag to expose a remote
unencrypted hop.

For the route-file commands and an HTTPS check, follow
[Traefik setup](traefik.md). This is needed only on the backup server.

4. Copy `systemd/vps-backup-gateway.service` to `/etc/systemd/system/`, then run:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now vps-backup-gateway
sudo systemctl status vps-backup-gateway
```

The supplied service reads `/etc/vps-backup/server.json`. Keep its cloud config
under `/etc/vps-backup` too; rclone may update provider tokens. No public route
or reverse-proxy configuration is created automatically. HTTPS routing is a
deployment prerequisite, not an installer side effect.

5. Enroll a VPS using a stable identifier:

```bash
sudo vps-backup --config /etc/vps-backup/server.json enroll --id my-vps
```

This creates `/etc/vps-backup/clients/my-vps.bundle.json` with separate random
gateway and encryption passwords and updates the bcrypt password file. rclone
reads updates without restarting. Reusing an ID is refused. Keep the recovery
bundle and server rclone config in a secure location off this server. Transfer
only the VPS's own bundle to it through SSH or another protected channel.

If enrollment is interrupted between writing the bundle and updating htpasswd,
do not regenerate the bundle. Its encryption password is the recovery key.
Repair the bcrypt entry from its `rest_password` with `htpasswd -iB`, passing
the password through standard input, rather than replacing the bundle.

## Target VPS

Copy the source directory and its enrollment bundle to the target. Run:

```bash
sudo bash install.sh --bundle /root/my-vps.bundle.json --enable-timer
```

The wizard scans immediate application directories under `/opt/dockers`,
excluding names beginning with `.` or `_`. It proposes SQLite databases
identified by file header among `.db`, `.sqlite`, and `.sqlite3` files. Confirm
each app. Non-SQLite database files require an explicit container-stop choice;
skipping that choice skips the app. Files with other names and external mounts
are not automatically classified. Review Docker mounts against the selected
paths; discovery is a proposal, not a guarantee of complete coverage.

Installation saves `/etc/vps-backup/client.json` privately and initializes the
remote repository in restic mode. It refuses to overwrite an existing client configuration or
repository. For automation, `setup-client --apps-file /root/apps.json` accepts
an explicitly reviewed JSON app array; that input file must also have mode 600.
Use `--base /another/parent` with `setup-client` to change discovery's root.

An app configuration looks like:

```json
{"id":"n8n","path":"/opt/dockers/n8n","sqlite":["n8n-data/database.sqlite"]}
```

For a database requiring a stop:

```json
{"id":"portainer","path":"/opt/dockers/portainer","stop_containers":["portainer"]}
```

For stopping containers, every writer of the app data must be named. SQLite
snapshots are internally consistent; database contents and separately written
application files are not guaranteed to represent one shared instant.

If you omit `--enable-timer`, installation leaves scheduling disabled. With
that flag, the installer uses `schedule --mode systemd`: daily at 06:00 in the
VPS timezone, up to five minutes of random delay, a 20-hour recent-success
window, and catch-up after downtime. It manages `vps-backup-client.timer`.
See [setup examples](setup.md) for changing it or combining it with n8n.
The older static templates under `systemd/vps-backup.*` use a different unit
name. Do not enable both sets of timers.

## Ordinary commands

```bash
sudo vps-backup doctor
sudo vps-backup run
sudo vps-backup status
sudo vps-backup snapshots
sudo vps-backup check --read-data
```

`doctor` checks local configuration, required sources, SQLite sizes, and space;
it makes no cloud requests. In restic mode, `run` saves one snapshot per app, then checks
repository metadata. In archive mode, each newly uploaded archive and manifest
is read back and checksum-verified before success is recorded. Unchanged apps
reuse their previous archive after checking that object's size and presence.
All requested apps and the mode's checks must succeed
for exit code 0. `status` reads the last saved result. An interrupted run may
already have saved some app snapshots; there is no whole-server transaction.
`check --read-data` downloads and verifies all stored packs and is more expensive
than the metadata check. Archive-mode `check` always downloads and verifies all
archives, even without `--read-data`. Backups never invoke `forget`, `prune`, or cloud delete.

`snapshots` displays a table; use `snapshots --json` for machine output.
`export --app APP_ID --to /path/app.tar.gz` produces a normal archive in either
mode. Add `--snapshot HEX_ID` to choose an older backup and `--json` for a machine
result. See [local recovery](local-recovery.md) to do this on your computer.
`run`, `status` and other operational commands retain JSON output for n8n.

## Disk use and interrupted runs

Preflight requires twice the total logical SQLite database size and stopped-app
copy size, plus a 32 MiB allowance and a configurable reserve. `reserve_mib`
defaults to 512 and must be at least 64. Set it higher for busy filesystems.
Archive mode additionally budgets twice the logical size of all selected app
files for archive creation. This is an estimate; other processes may consume space after it passes.

Snapshots, stopped-app copies, and restic temporary files live under
`/var/lib/vps-backup/work`. Free space is checked during SQLite copies and while
restic runs. Stop-mode copies check space before each copied file. There is no
full archive in restic mode and no persistent restic cache. Ordinary files are read directly.
Archive mode builds its `.tar.gz` under `work`, hashing the bytes written to it.
Changes to content, names, permissions, ownership or link targets count as changes;
timestamps alone do not. A file detected changing during creation fails the backup.

The work directory is removed after a run, including an upload failure. A later
run cleans work left by a crash before creating new snapshots. Container stop
intent is journaled in `stopped.json`; recovery restarts those containers before
preflight. A failed restart preserves the journal and fails the run. SIGKILL,
power loss, and host failure cannot execute cleanup; recovery happens on the
next run, so external monitoring is still needed. Do not delete a stop journal
without checking those containers.

## Scheduling and n8n

[Setup examples](setup.md#n8n-only) cover n8n, systemd, and fallback schedules.
`run --min-age-hours N` skips after a successful backup of the same configured
apps, storage mode and repository within N hours. A plain `run` defaults to zero and always
attempts a backup; `--force` bypasses an explicit guard.
The check happens under the lock, after interrupted-run recovery and cleanup.
A skip returns exit 0, `status: "success"`, `stage: "skipped"`, and `skipped: true`.

`last-success.json` records the completion timestamp and configuration hash.
Failed runs never update it. Missing, invalid, or future-dated history does
not suppress a backup. `last-run.json` records the latest attempt, including
skips; `status` reads that file. File changes inside an unchanged app
configuration do not invalidate the time window.

Keep the guard shorter than the backup interval. Allow enough time between
primary and fallback schedules for a normal run to finish. Set n8n's execution
timeout to cover the SSH command, within the maximum allowed by the instance.
The template requires SSH exit code 0 and JSON `status: "success"`; transport,
parsing, and backup failures fail the execution. It sends no notifications.
Cloud credentials remain on the gateway.

## Failures

For a nonzero restic exit, inspect the root-only
`/var/lib/vps-backup/last-error.log` on the target; it contains the last 64 KiB of
stderr from the failed command. It can include private paths and is not sent
to n8n. It remains until a later failure replaces it, so compare it with the
current saved run. Timeouts and launch failures have no stderr log.

For insufficient disk space, check `doctor` and the filesystem's current use.
Do not remove cloud snapshots to solve a target's scratch-space problem.
For gateway errors, inspect `journalctl -u vps-backup-gateway` on the server and
verify its HTTPS route and provider access. The gateway's private temporary
directory is not a durable queue: a cloud failure makes the upload fail, and
the next run takes a new source snapshot.
