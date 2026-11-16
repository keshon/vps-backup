# VPS Backup

Back up Docker application directories with restic through a central rclone
gateway. Cloud storage credentials stay on the gateway. Each VPS receives its
own repository password and gateway login. n8n can start runs over SSH and read
their JSON results.

The client creates consistent SQLite copies, checks scratch space, uploads
deduplicated snapshots, and removes temporary files after success or failure.
It does not create a complete archive or keep a persistent restic cache.
Applications requiring a stop are copied locally, restarted, and then uploaded.

This is an initial implementation for Debian/Ubuntu. Its fixture tests cover
backup, deduplication, SQLite WAL recovery, and verified restore through an
authenticated gateway into a union of two local directories. An opt-in test of
the same sequence also passed against the Koofr union using disposable fixture
data. A public HTTPS gateway also passed a backup/restore test. Automatic
retention and a ready-made n8n workflow are not included in this version.

## Start

Download or copy this complete repository to the machine before running its
installer. Do not pipe the installer to bash: client setup needs a terminal.

On the backup server:

```bash
sudo bash install.sh --role server
```

Then [configure the gateway](docs/running.md#backup-server) and enroll a VPS.
On that VPS, install with its private enrollment bundle:

```bash
sudo bash install.sh --bundle /root/my-vps.bundle.json --enable-timer
```

The wizard asks which discovered applications to include. It initializes the
remote repository; the optional timer runs daily. Existing installations are
not overwritten. In particular, an existing `/usr/local/bin/vps-backup` from
the previous prototype must be migrated before installation.

- [Setup and ordinary operation](docs/running.md)
- [Architecture and credential boundaries](docs/architecture.md)
- [Restore and recovery](docs/recovery.md)
- [Development and checks](docs/conventions.md)
