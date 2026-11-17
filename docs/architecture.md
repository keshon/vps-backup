# Architecture

```text
Target VPS: files + consistent database copies
    restic: chunk, deduplicate, encrypt
        HTTPS with per-VPS authentication
            Backup server: rclone serve restic
                private repositories + append-only
                    Koofr union: encrypted repository objects
```

The gateway streams repository writes to the configured remote. It does not
stage a complete repository or periodically mirror one to cloud storage.
Provider/backend buffering can still use memory or temporary space. The
default end-to-end fixture test exercises two local union upstreams. An opt-in
run against the Koofr union also passed backup, deduplication, full data checking,
and restore with an 8 MiB random file and a live SQLite WAL database. It used a
dedicated test prefix, which was removed afterward. Cloud quotas, provider
outages, and full-sized application backups still need deployment validation.

## Ownership

The flow above describes restic mode. Archive mode shares source preparation
but builds an ordinary app-rooted `.tar.gz`, fingerprints its contents and relevant
metadata, and uploads only when changed. Objects live under each client's
`archives/data/`, with committed manifests under `archives/snapshots/`. Both use
the authenticated append-only gateway; native archives are plaintext in storage.
New uploads and manifests are read back and SHA256-verified before success.
An interrupted upload may leave an unreferenced object; it cannot become a saved
snapshot without a completed manifest. No automatic garbage collection runs.

`archive.py` owns native encoding, change detection and gateway object access.
`recovery.py` owns snapshot selection and portable exports for both modes.
Restic exports stream through tar conversion into gzip without unpacking locally;
archive exports download and verify the stored object. `recover.py` is the
Windows/Linux entry point, using the same recovery operations as the VPS CLI.

`client.py` owns source checks, scratch state, database snapshots, container
recovery, and restic execution. Each app produces an independently addressable
snapshot. SQLite copies and stopped-app copies have stable paths under `work`
so subsequent runs can deduplicate their contents. Other files retain their
original absolute paths in snapshots. Restoration does not overwrite sources.

`server.py` owns gateway configuration, the safety audit, and client enrollment.
It retains a per-VPS recovery bundle before enabling that VPS's gateway login.
The rclone binary's SHA256 is recorded only after its local behavior audit
passes; serving refuses a different binary. A successful fixture audit is not
a security certification of the binary or remote backend.

`onboarding.py` owns recovery-key import, consistent app selection and resumable
client setup. `installation.py` publishes runtime updates under the client lock,
preserves the gateway binary, and restores the old runtime if publication fails.
`cli.py` owns parsing and dispatch; `presentation.py` owns grouped help, actionable
diagnostics and terminal summaries. Non-terminal operational output keeps the
JSON contract used by automation. No credential values are printed
in normal results. Subprocesses receive restic secrets in environment variables,
not command arguments. The client clears inherited restic/rclone overrides.
Configuration and diagnostic files are private to root.

## Access boundaries

The backup server holds Koofr credentials and all enrolled recovery bundles.
Each target holds its own restic encryption password and gateway login. It
cannot use its login to access another user's repository. The endpoint refuses
replacement of existing objects and deletion of non-lock objects. Lock removal
is allowed because restic must release locks after operations.

Append-only access reduces deletion risk; it does not prevent a compromised
client from leaking its own data, uploading bad snapshots, exhausting space,
or interfering with its repository's locks. A compromised backup server can
access cloud credentials and recovery bundles. The Koofr union pools capacity;
it is not redundant storage. These boundaries assume validated HTTPS, private
configuration permissions, and the gateway being reached through its intended
endpoint.

## Retention

Automatic retention is intentionally absent in this version. Targets have no
delete-capable gateway access, and clients must never run pruning. A future
central maintenance command needs separate cloud access, coordination with
active backups, retention rules, and post-prune integrity checks. Enabling an
age-based cloud cleanup outside restic would break shared data references.

## Scheduling

Either n8n or systemd can schedule backups. Both can call the same client
command with a recent-success window for fallback scheduling. `schedule.py`
manages the local client timer; n8n schedules remain in n8n.
A per-VPS flock serializes this tool's
operations. It does not serialize the old Bash scripts or arbitrary manually
invoked restic commands; keep those out of the same repository.
