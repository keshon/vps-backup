# VPS Backup

Back up Docker folders from several VPS to cloud storage. Restic encrypts the
backups and uploads changed chunks. Cloud credentials stay on one backup server.

Start with [the setup guide](docs/setup.md). It covers adding a VPS and includes
copyable examples with n8n, without n8n, and with a systemd fallback.

The client checks free space and removes temporary copies after each run.
SQLite can be copied while the app runs. Other databases may need a brief
container stop; the app restarts before the upload.

This version supports Debian/Ubuntu. Automatic retention is not implemented,
so stored backups keep growing until you arrange central maintenance.

- [Setup examples](docs/setup.md)
- [Configuration and troubleshooting](docs/running.md)
- [Restore a backup](docs/recovery.md)
- [How it works](docs/architecture.md)
- [Development](docs/conventions.md)
