# Development

Python standard library only. The client and gateway run on Linux; local recovery
also runs on Windows. The installer targets Debian/Ubuntu and currently installs
the gateway binary on amd64 or arm64.

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q vpsbackup recover.py
bash -n install.sh
sh -n recover.sh
node --test tests/test_notifications.cjs
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
disk-reserve failure. [Linux CI](../.github/workflows/tests.yml) installs and
reinstalls the tool on a disposable runner and runs Python and notification tests.
The setup tests cover interrupted initialization, wrong-key protection, copied
bundles, malformed config, app-selection cancellation and CLI compatibility.
The optional Node.js check executes the actual code embedded in the n8n template
with fixtures. It checks completion/skip summaries, HTML escaping, message limits,
capacity failures and credential-free independent notification branches. Node.js
is needed for this development check, not for the VPS client.
The integration fixture never reads production configuration or connects to Koofr
unless the explicit cloud-test variables below are supplied.
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
