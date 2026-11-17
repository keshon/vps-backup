# Development

Python standard library only. The client and gateway run on Linux; local recovery
also runs on Windows. The installer targets Debian/Ubuntu and currently installs
the gateway binary on amd64 or arm64.

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q vpsbackup recover.py
bash -n install.sh
sh -n recover.sh
```

`test_integration.py` runs when Linux, restic, rclone, and htpasswd are available;
otherwise unittest reports it skipped. Use a rclone binary that passes
`python3 vps-backup audit-gateway`. The integration test creates temporary local
union storage, authentication entries, an encrypted repository, and a live WAL
database. It verifies deduplication on a second backup, excludes the live
database, restores the consistent copy and file bytes, and reads all data packs.
It also exports a portable restic archive, backs up in native archive mode,
checks that unchanged content reuses the saved archive, and exports its bytes.
Unit tests cover invalid manifests, checksum failures and export cleanup after a
disk-reserve failure. These checks are local commands; no CI workflow currently
runs them automatically.
By default it never reads production configuration or connects to Koofr.
An explicit cloud test can set `VPS_BACKUP_TEST_REMOTE` to a new
`REMOTE:vps-backup-validation-*` prefix and `VPS_BACKUP_TEST_RCLONE_CONFIG` to
the server's private config. This uploads random fixture bytes, not application
data. Remove only that test prefix afterward using the server's cloud access;
the test does not perform cloud deletion or retain recovery credentials for
these disposable fixtures.

Source temporary data must remain under the configured state directory and
must not overlap a source. Cleanup must run on failure, while a failed
container restart must preserve its recovery journal. Keep these tests when
changing subprocess, stop, snapshot, or cleanup behavior. Do not add deletion
to the client; remote retention belongs to central maintenance with separate
validation and coordination.

The gateway audit is an operational prerequisite recorded by binary hash.
If a gateway version changes, retest its auth, isolation, overwrite, deletion,
traversal, lock lifecycle, and complete backup/restore behavior before saving
its new fingerprint. Do not bypass the audit to make an old binary start.

## Code and documentation

Project-local [Python style](../.agents/skills/python-style/SKILL.md) and
[architecture](../.agents/skills/python-architecture/SKILL.md) describe maintained
code practices. These are review guidance, not lint rules. Keep source preparation
and restart recovery shared; keep encoding and remote-layout details with the
storage backend. Portable recovery imports must not require Linux-only modules.

The [documentation pattern](../.agents/skills/docs-pattern/SKILL.md) assigns each
subject a canonical guide. Check relative links and CLI examples after changing
commands. Never commit recovery bundles, private configuration or application
archives; archive filename patterns are ignored as an accidental-add safeguard.
