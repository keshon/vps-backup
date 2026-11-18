# VPS Backup

A Go terminal app for backing up app folders as standalone `.tar.gz` archives.
It uses Gektor's terminal components for app selection, forms, popups and layout.
The backup workflow is Go; rclone handles cloud providers and union storage.

## Try it

From this checkout with Go 1.26.4 or newer:

```sh
go run ./cmd/vps-backup
```

1. Open **Settings**. Set a unique server name, a destination such as
   `union-koofr:backups`, and a working folder outside your source folders.
2. **Add app** for each folder. List the writer containers to stop when backing
   up databases or WAL stores. Leave that list empty for static files.
3. Open **Credentials** and paste your base64-encoded rclone configuration.
   It stays in memory for this session. Include the union and its upstreams.
4. Choose **Back up** and confirm. **Activity** shows progress and results.

Use the mouse or the shortcuts shown on the buttons. Arrow keys select apps;
`/` filters them. Forms use Tab, Shift+Tab, Enter, Ctrl+S and Escape. Their fields,
Save/Cancel buttons and confirmation buttons are clickable. Scroll the activity
with the wheel or Up/Down. Cancelling or quitting during a run waits for cleanup
and container restart attempts before leaving the terminal.

Settings save under your user configuration folder in `vps-backup/config.json`.
Use `--config /absolute/path/config.json` to choose another location. The TUI
creates settings when you save; no enrollment key or configuration template is
required. Credentials are never included in that file.

For a binary:

```sh
go build -o dist/ ./cmd/vps-backup
```

Run `dist/vps-backup` on Linux or `dist/vps-backup.exe` on Windows. The CI workflow
also provides Windows and Linux binaries as build artifacts. No service, timer,
gateway, Python runtime or Bash backup script is installed. On Ubuntu, a backup
started as root installs rclone through apt if missing; other users get the exact
installation command. Docker must already be installed for container stops.

## What a backup does

Each app is fingerprinted by streaming a deterministic tar archive through SHA-256.
Unchanged apps are skipped after checking that the previous remote archive is
still present with the expected size. A changed app gets a complete compressed
archive. Containers configured for that app are stopped during fingerprinting and
archiving, including the fingerprint scan on unchanged runs, and restarted before
cloud access. Containers that were already stopped stay stopped.

Uploads are downloaded again and SHA-256 compared. Only verified uploads update
the saved fingerprint. Each archive has a `.sha256` completion file. Retention
keeps the configured number per server/app and ignores incomplete uploads without
that file. Failed retention is retried on the next unchanged run.

The working folder holds fingerprints, the last run report, a restart journal if
needed, and temporary archives. A local OS lock prevents simultaneous runs; its
file can remain on disk without holding the lock. Ordinary failures and
cancellation clean scratch files. SIGKILL or power loss can leave scratch folders
and a restart journal; the next run attempts the recorded restarts. A failed
restart keeps that journal and reports the exact `docker start` command.

Scratch space is estimated before compression. Other processes can consume space
concurrently, so this is not a filesystem reservation. Archives preserve ordinary
file modes, Linux ownership, symlinks and empty directories. This first version
does not preserve ACLs or extended attributes and does not create online database
snapshots. Stop every writer for transactional data. Files outside the selected
folder, including symlink targets, are not copied automatically.

The full readback doubles archive transfer traffic. Archives are ordinary files,
not encrypted by this app. Remotes with static credentials, such as Koofr, fit
the memory-only configuration transport; refreshed OAuth tokens are not saved.

## Restore and automation

Cloud layout is `REMOTE/SERVER/APP/TIMESTAMP-RANDOM.tar.gz`. Download an archive
and its checksum using the provider UI or rclone, then:

```sh
sha256sum -c ARCHIVE.tar.gz.sha256
mkdir restored
tar -xzpf ARCHIVE.tar.gz -C restored
```

The archive contains that app folder's contents. Restore into an empty folder,
inspect it, and stop the destination app before replacing its data. Linux is
needed to restore Linux ownership; use root and `--numeric-owner` when required.

For unattended execution, supply `RCLONE_CONFIG_CONTENT` as base64 in the process
environment and run `vps-backup --run --config PATH`. JSON goes to stdout and
progress to stderr; failure exits nonzero. The app converts cloud configuration
to rclone environment settings and uses the null device as its config file. Do
not use shell tracing around credentials. Your orchestrator may retain supplied
environment values in its execution records.

The separately maintained [Bash archive tool](https://github.com/keshon/backup-to-remote)
retains the n8n workflow and Discord/Telegram notification design. This Go app
does not install or modify that workflow. Do not stop an n8n container from a
workflow running inside that same container.

## Development and reuse

```sh
go test ./...
go test -race ./...
go vet ./...
```

The rclone roundtrip test uses disposable local storage and skips if rclone is
missing. It does not contact Koofr. See [component provenance](internal/tui/components/README.md)
for the Gektor reuse boundary and [AGENTS.md](AGENTS.md) for ownership and Go skills.

This replaces the earlier Python/REST design. Its source remains in the Git tag
`archive/python-before-go-2026-11-18`; installed instances and cloud repositories
are not migrated or removed. Validate with disposable data before relying on a
new deployment.
