# Which command do I need?

Run `vps-backup` by itself to see whether this machine needs setup and where to
start. `vps-backup --help` groups the commands by task. On a VPS, use `sudo` when
your account cannot read the private configuration.

| I want to… | Command | What happens |
| --- | --- | --- |
| Finish connecting this VPS | `vps-backup setup` | Imports its recovery key, selects folders, and prepares storage; safe to retry |
| Check this VPS before backing up | `vps-backup preflight` | Checks folders, database sizes, required programs and local space; no cloud access |
| Back up now | `vps-backup backup` | Takes consistent copies, uploads, verifies metadata, and cleans temporary files |
| See the last result | `vps-backup status` | Reads local history; does not check the cloud or create a backup |
| Understand a failure | `vps-backup logs` | Reads the private log, including the backup engine's stdout and stderr |
| See saved backups | `vps-backup list` | Lists cloud snapshots by app and date |
| Download normal files | `vps-backup export --app APP_ID` | Assembles a `.tar.gz`; never overwrites an existing archive |
| Check stored backups | `vps-backup verify` | Contacts storage and checks integrity; can take several minutes |
| Check all stored bytes | `vps-backup verify --read-data` | Downloads and verifies all data; archive mode always does this |
| Change included folders | `vps-backup apps --select` | Lets you select again, then confirm before saving |
| Change automatic runs | `vps-backup schedule --help` | Explains manual, systemd, n8n and fallback modes |
| Change storage format | `vps-backup storage archive` or `restic` | Keeps existing backups and schedules |

`status` answers **what happened last time**. `verify` answers **whether the stored
backups pass an integrity check now**. Neither starts a backup.

## On the shared backup server

| Task | Command |
| --- | --- |
| Configure cloud access and the HTTPS gateway | `vps-backup setup-server --help` |
| Create a target VPS's recovery key | `vps-backup enroll --id my-vps` |
| Read cloud capacity | `vps-backup cloud-space` |

These commands use `/etc/vps-backup/server.json` automatically. Client commands
use `/etc/vps-backup/client.json`. `--config PATH` overrides either default.

## Scripts and older commands

In a terminal, results are readable summaries and progress appears on stderr.
Use `--json` for scripts. Redirected/non-terminal output retains JSON by default
for operational commands; `list`, `export` and `logs` keep their readable default
unless `--json` is supplied. Setup prompts and diagnostics stay on stderr.

Existing automation names remain accepted: `run` means `backup`, `snapshots`
means `list`, `doctor` means `preflight`, `check` means `verify`, and `setup-client`
means `setup`. You do not need to rewrite existing n8n or systemd jobs.

`init` is an advanced repository-only operation. It cannot create missing client
settings or generate a recovery key. Use `setup` for onboarding.
