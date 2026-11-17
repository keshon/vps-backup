# Recover on your computer

Download or clone this repository onto your Windows or Linux computer. Keep the
whole folder: the launcher needs `recover.py` and `vpsbackup/` beside it. You need
Python 3.10+ and your VPS's `*.bundle.json` recovery key. Keep the key private.

## Choose a backup

On Windows, double-click `recover.cmd`. On Linux, open a terminal in the repository:

```bash
sh recover.sh
```

The helper looks for keys in the current folder and `~/.ssh/vps-backup-recovery`.
If it finds none, it asks for the key's path. It lists backups from both storage
modes, asks which to save, and proposes a filename in Downloads. The result is
a normal `.tar.gz` containing an `APP_ID/` folder. Open it with 7-Zip or tar.
Restic snapshots are assembled while streaming; there is no full unpacked copy.
Native archives are downloaded and checked against their recorded SHA256.

For restic snapshots, Linux needs restic installed (`sudo apt install restic` on
Debian/Ubuntu). On Windows the helper uses an installed restic or downloads a
pinned official release once, checking it against the publisher's HTTPS checksum.
Use `--mode archive` to work with native archives without restic.

## Example calls

Run these in the downloaded repository. Use `python3` on Linux if needed:

```bash
python recover.py --bundle /path/to/my-vps.bundle.json --list
python recover.py --bundle /path/to/my-vps.bundle.json --mode archive --list
python recover.py --bundle /path/to/my-vps.bundle.json --app melodix --to melodix.tar.gz
```

In PowerShell, a Windows key path can be passed as
`--bundle 'C:\Users\me\Keys\my-vps.bundle.json'`.

The destination must not exist. Allow space for the compressed archive plus at
least 64 MiB free. The exported files are decrypted; protect the resulting archive
like the original application data.

## If the backup server is unavailable

For restic, download the complete repository folder from cloud storage, then run:

```bash
python recover.py --bundle /path/to/my-vps.bundle.json --repository /path/to/downloaded-repository
```

The repository is the VPS folder containing restic's `config`, `data`, `index`,
`keys` and `snapshots`, not its parent. The saved bundle supplies the encryption
password. For archive mode, download the `.tar.gz` directly from
`REMOTE_PREFIX/SERVER_ID/archives/data/` and unpack it. See
[server-loss recovery](recovery.md#gateway-lost) for direct cloud access.

Inspect the files before replacing live data. Restoring services, ownership and
database files on a VPS follows [the recovery guide](recovery.md).
