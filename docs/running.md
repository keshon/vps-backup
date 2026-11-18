# Running and restoring

## Settings

Settings save when you choose Save in the TUI. The default file is
`~/.config/vps-backup/config.json` on Linux and
`%AppData%\vps-backup\config.json` on Windows.

Choose another file with:

```sh
./vps-backup --config /absolute/path/config.json
```

Use **Add app**, **Edit** and **Remove** to change the backup list. Removing an
app does not delete its cloud archives. Excludes are relative paths or globs.
Files outside the chosen folder, including symlink targets, are not copied.

List every container that writes database or WAL data under **Containers to
stop**. Leave it empty for static files. Containers already stopped stay stopped.

To prepare credentials, see [n8n setup](n8n.md#set-up). Paste the same base64
value into the TUI's **Credentials** field. Credentials are not saved in settings.
Refreshed OAuth tokens are not persisted; static credentials such as Koofr fit
this approach.

## Backup behaviour

Each run scans every app. Unchanged apps are skipped if their previous archive
still exists with the expected size. Changed apps get a complete `.tar.gz`.
Configured containers stop during scanning and archiving, even on unchanged
runs, and restart before cloud access.

Uploads are read back and checked with SHA-256 before the app records success.
This doubles archive transfer traffic. Each completed archive has a `.sha256`
file. Retention keeps the configured number per app and ignores incomplete
uploads without that file. Retention failures retry on the next run.

The working folder stores fingerprints, `last-run.json` and temporary archives.
Scratch space is checked before compression, but other processes can still fill
the disk. Archives preserve ordinary modes, Linux ownership, symlinks and empty
directories; ACLs and extended attributes are not preserved. Archives are not
encrypted by this app. There are no online database snapshots.

## Restore

Find files at `REMOTE/SERVER/APP/TIMESTAMP-RANDOM.tar.gz`. Download the archive
and its matching `.sha256` file into the same folder. Then run on Linux:

```sh
sha256sum -c ARCHIVE.tar.gz.sha256
mkdir restored
tar -xzpf ARCHIVE.tar.gz -C restored
```

The archive contains the app folder's contents. Inspect the restored files, stop
the destination app, then replace its data. Use root and `--numeric-owner` when
restoring Linux ownership.

## If a run fails

Read **Activity**, or stderr from an unattended run. The last report is also in
`last-run.json` inside the working folder.

Cancel and Quit wait for temporary-file cleanup and container restart attempts.
After power loss or forced termination, the next run attempts restarts recorded
in its journal. A failed restart keeps the journal and shows a `docker start`
command. Forced termination can also leave `run-*` scratch folders: remove them
only when no backup is running.

A local OS lock prevents overlapping runs. It releases when the process exits;
the lock file can remain on disk safely. Do not delete it to bypass a running
backup.
