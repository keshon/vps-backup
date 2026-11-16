# Restore and recovery

Keep the server's rclone configuration and every enrollment bundle in a secure
location off the backup server. Both storage access and the repository password
are needed if the gateway is lost. Restic cannot recover a lost encryption
password. An enrollment bundle has no Koofr credentials.

## Test a snapshot

On a configured client:

```bash
sudo vps-backup snapshots
sudo vps-backup restore --snapshot EXPLICIT_HEX_ID --to /var/tmp/restore-review
```

Replace `EXPLICIT_HEX_ID` with the selected snapshot's ID. The destination must
be empty and cannot overlap any configured source or the state directory.
Restic verifies restored file contents. The command does not restart services
or replace live files. Allow enough destination space before a restore: unlike
backup preflight, this version does not estimate restore size.

Snapshots preserve absolute path structure under the destination. Ordinary
application files appear under their original path, for example
`/var/tmp/restore-review/opt/dockers/n8n/...`. SQLite snapshots instead appear
under `/var/tmp/restore-review/var/lib/vps-backup/work/sqlite/n8n/...` with the
configured database-relative path appended. The live database and its WAL,
SHM, and rollback-journal files were excluded from the ordinary source copy.
Apps configured to stop are restored under
`/var/tmp/restore-review/var/lib/vps-backup/work/stopped/APP_ID/...`.
These examples assume the default state directory.

Check database copies with SQLite `PRAGMA integrity_check` and inspect expected
application records. To restore production, stop all writers, copy the reviewed
application files and database snapshot to their configured locations, and
remove stale SQLite sidecars belonging to the replaced database before starting
writers again. Preserve original permissions and ownership. Do not copy a
snapshot over a running database. Container/database-specific production
recovery remains an operator action in this version.

## Gateway lost

Rebuild a gateway using the saved rclone configuration, or use a trusted recovery
machine with restic and rclone installed. A cloud repository is located at:

```text
REMOTE_PREFIX/SERVER_ID/
```

Restic can access it directly with its rclone backend, for example
`rclone:union-koofr:restic-vps/my-vps`. Provide the private rclone configuration
through `RCLONE_CONFIG` and the bundle's repository password through a private
`RESTIC_PASSWORD_FILE`. Do not put passwords into shell history or command
arguments. First list snapshots and check the repository, then restore into a
separate directory. Do not reinitialize an existing repository.

## Migration from the old runner

The installer refuses to overwrite `/usr/local/bin/vps-backup`. If a previous
runner occupies this path, preserve its source, configuration, state, and
pending archives before arranging a
replacement command. A server-only gateway can coexist using the installer's
`--command-name vps-backup-restic` option. Do not remove the old pending queue
as installation cleanup.

Deploy the new gateway into a new remote prefix. Run a pilot with selected apps,
perform a full data check and a database/file restore, and review Docker mounts
for omissions. Only then replace the old schedule. Keep old ZIPs and prototype
archives until the new recovery path is established. This repository does not
make that migration or delete legacy artifacts automatically.
