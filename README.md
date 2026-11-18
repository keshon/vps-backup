# VPS Backup

Back up app folders to cloud storage as `.tar.gz` archives. Configure and run
backups in a Go terminal app, or schedule them with n8n. rclone supports Koofr
and union storage. Only changed apps get a new archive.

## Start

With Go 1.26.4 or newer, run on the machine you want to back up:

```sh
go build -o vps-backup ./cmd/vps-backup
./vps-backup
```

On Windows, build with `-o vps-backup.exe` and run `.\vps-backup.exe`.
Windows and Linux binaries are also available as GitHub Actions build artifacts.

1. **Settings**: choose a server name, cloud destination (for example
   `union-koofr:backups`), and working folder outside your app folders.
2. **Add app**: select a folder. For databases, list the containers that write
   to it. They stop during copying and restart before uploading.
3. **Credentials**: paste your base64 rclone configuration, including any union
   upstreams. It stays in memory for this session.
4. **Back up**: confirm. Open **Activity** to see results.

Use the mouse or the shortcuts on the buttons. Tab moves between fields;
Ctrl+S saves and Escape cancels. The mouse wheel scrolls activity.

Ubuntu root runs install rclone through apt if missing. Other users get an
installation command. Container stops require Docker access.

## Guides

- [Schedule with n8n and send Discord/Telegram notifications](docs/n8n.md)
- [Settings, restore and backup behaviour](docs/running.md)

## Development

```sh
go test ./...
go test -race ./...
go vet ./...
node --test n8n/workflow.test.cjs
```

Cloud tests use disposable local rclone storage and skip if rclone is missing.
The TUI reuses [Gektor components](internal/tui/components/README.md).
See [AGENTS.md](AGENTS.md) for contributor rules.

The previous Python/restic implementation remains in tag
`archive/python-before-go-2026-11-18`. Existing installations are not migrated.
