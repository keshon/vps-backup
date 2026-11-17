#!/usr/bin/env bash
set -euo pipefail
umask 077

role=client
bundle=''
enable_timer=0
storage_mode=''
config=''
install_only=0
command_name=vps-backup
usage() {
    cat <<'EOF'
VPS Backup installer (Debian/Ubuntu)

Start a client setup:
  sudo bash install.sh
  sudo bash install.sh --bundle /root/my-vps.bundle.json

The recovery key is created on the BACKUP SERVER with:
  vps-backup enroll --id my-vps
Copy its .bundle.json to this VPS. You do not need to create client.json yourself.

Options:
  --role client|server         Client is the default; server installs the gateway tools
  --bundle PATH                Existing recovery key for this VPS
  --config PATH                Where to save client settings (default: /etc/vps-backup/client.json)
  --storage-mode restic|archive Default: keep existing mode, or restic for a new client
  --enable-timer               Enable a daily 06:00 systemd timer after setup succeeds
  --install-only               Install/update commands now; configure later with vps-backup setup
  --command-name NAME          Advanced: alternate command name for a server-only install
  --help                      Show this guide

Already installed? Run this installer again: it updates this tool and resumes setup.
Configuration, recovery keys, schedules and the gateway binary are preserved.
EOF
}
need_value() {
    if [[ $# -lt 2 || -z $2 || $2 == --* ]]; then
        printf 'error: %s needs a value.\nhelp: Run bash install.sh --help for examples.\n' "$1" >&2
        exit 2
    fi
}
while (($#)); do
    case "$1" in
        help|-h|--help) usage; exit 0 ;;
        --role) need_value "$@"; role=$2; shift 2 ;;
        --bundle) need_value "$@"; bundle=$2; shift 2 ;;
        --config) need_value "$@"; config=$2; shift 2 ;;
        --storage-mode) need_value "$@"; storage_mode=$2; shift 2 ;;
        --enable-timer) enable_timer=1; shift ;;
        --install-only) install_only=1; shift ;;
        --command-name) need_value "$@"; command_name=$2; shift 2 ;;
        *) printf 'error: Unknown option: %s\nhelp: Run bash install.sh --help for examples.\n' "$1" >&2; exit 2 ;;
    esac
done
[[ $EUID == 0 ]] || { echo 'Run as root.' >&2; exit 1; }
[[ $role == client || $role == server ]] || { echo 'Unknown role.' >&2; exit 1; }
[[ $command_name =~ ^vps-backup(-[a-z0-9-]+)?$ ]] || { echo 'Invalid command name.' >&2; exit 1; }
[[ $command_name == vps-backup || ( $role == server && -z $bundle && $enable_timer == 0 ) ]] || { echo 'Alternate command names are supported for server-only installs.' >&2; exit 1; }
[[ -z $bundle || $role == client ]] || { echo '--bundle applies only to clients.' >&2; exit 1; }
[[ $enable_timer == 0 || ( $role == client && $install_only == 0 ) ]] || { echo 'error: --enable-timer needs client setup. Remove --install-only and retry.' >&2; exit 1; }
[[ -z $storage_mode || $storage_mode == restic || $storage_mode == archive ]] || { echo "Unknown storage mode." >&2; exit 1; }
command -v apt-get >/dev/null || { echo 'This installer supports Debian/Ubuntu.' >&2; exit 1; }
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
[[ -f $source_dir/vpsbackup/cli.py ]] || { echo 'Download the complete source directory first.' >&2; exit 1; }
[[ -n $config ]] || config=/etc/vps-backup/$role.json
[[ $config == /* ]] || { echo 'error: --config must be an absolute path, such as /etc/vps-backup/client.json.' >&2; exit 1; }
if [[ -n $bundle && ! -f $bundle ]]; then
    printf 'error: Recovery key not found: %s\nThe key must already exist; it is not the output config file.\nCreate it on the backup server with vps-backup enroll, copy it here, then retry.\n' "$bundle" >&2
    exit 1
fi
if command -v python3 >/dev/null; then
    PYTHONPATH="$source_dir" python3 - "$command_name" <<'PY'
from pathlib import Path
import sys
from vpsbackup.installation import owned_install
from vpsbackup.common import BackupError
try:
    owned_install(Path('/usr/local/lib/vps-backup'), Path('/usr/local/bin') / sys.argv[1])
except BackupError as exc:
    sys.exit('error: ' + str(exc))
PY
fi
packages=(python3 restic ca-certificates)
[[ $role != server ]] || packages+=(apache2-utils curl)
apt-get update
apt-get install -y --no-install-recommends "${packages[@]}"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else "Python 3.10+ required")'
stage_dir=$(mktemp -d /usr/local/lib/.vps-backup.XXXXXXXX)
wrapper_tmp=''
cleanup() {
    [[ -z $stage_dir ]] || rm -rf -- "$stage_dir"
    [[ -z $wrapper_tmp ]] || rm -f -- "$wrapper_tmp"
    return 0
}
trap cleanup EXIT
cp -R -- "$source_dir/vpsbackup" "$stage_dir/"
find "$stage_dir" -type d -exec chmod 755 {} +
find "$stage_dir" -type f -exec chmod 644 {} +
if [[ $role == server && ! -x /usr/local/lib/vps-backup/bin/rclone ]]; then
    # Keep a pinned gateway binary separate from rclone used by existing jobs.
    machine=$(uname -m)
    case "$machine" in
        x86_64) arch=amd64 ;;
        aarch64) arch=arm64 ;;
        *) echo 'Gateway installer supports amd64 and arm64.' >&2; exit 1 ;;
    esac
    install -d -m 755 "$stage_dir/bin"
    python3 - "$arch" "$stage_dir/bin/rclone" <<'PY'
import hashlib
from pathlib import Path
import sys
import subprocess
import zipfile
import io
version = '1.76.0'
name = f'rclone-v{version}-linux-{sys.argv[1]}'
base = f'https://downloads.rclone.org/v{version}/'
def download(url):
    return subprocess.check_output(['curl', '--fail', '--silent', '--show-error', '--location',
                                    '--proto', '=https', '--proto-redir', '=https',
                                    '--retry', '3', '--max-time', '120', url])
archive = download(base + name + '.zip')
sums = download(base + 'SHA256SUMS').decode()
expected = next(line.split()[0] for line in sums.splitlines()
                if line.strip() and line.split()[-1].lstrip('*') == name + '.zip')
if hashlib.sha256(archive).hexdigest() != expected:
    sys.exit('rclone checksum mismatch')
with zipfile.ZipFile(io.BytesIO(archive)) as package:
    binary = Path(sys.argv[2])
    binary.write_bytes(package.read(name + '/rclone'))
    binary.chmod(0o755)
PY
fi
wrapper_tmp=$(mktemp /usr/local/bin/.vps-backup.XXXXXXXX)
cat > "$wrapper_tmp" <<'EOF'
#!/bin/sh
export PYTHONPATH=/usr/local/lib/vps-backup
exec python3 -c 'import sys; sys.path.insert(0, "/usr/local/lib/vps-backup"); from vpsbackup.cli import main; raise SystemExit(main())' "$@"
EOF
chmod 755 "$wrapper_tmp"
PYTHONPATH="$source_dir" python3 - "$stage_dir" "$wrapper_tmp" "$command_name" "$config" <<'PY'
from pathlib import Path
import sys
from vpsbackup.installation import publish
from vpsbackup.common import BackupError
try:
    result = publish(Path(sys.argv[1]), Path(sys.argv[2]), Path('/usr/local/lib/vps-backup'),
                     Path('/usr/local/bin') / sys.argv[3], Path(sys.argv[4]), Path('/var/lib/vps-backup'))
except BackupError as exc:
    sys.exit('error: ' + str(exc) + '\nhelp: If a backup is running, wait for it to finish and retry.')
print('Commands updated.' if result['updated'] else 'Commands installed.')
if result['previous_runtime']:
    print('Previous runtime kept at: ' + result['previous_runtime'])
PY
stage_dir=''
wrapper_tmp=''
install -d -m 700 /etc/vps-backup /var/lib/vps-backup
if [[ $role == client && $install_only == 0 ]]; then
    setup_args=(setup --config "$config")
    [[ -z $bundle ]] || setup_args+=(--bundle "$bundle")
    [[ -z $storage_mode ]] || setup_args+=(--storage-mode "$storage_mode")
    if ! /usr/local/bin/vps-backup "${setup_args[@]}"; then
        printf '\nThe command is installed. Setup is unfinished; no reinstall is needed.\nResume with:\n  vps-backup setup --config %q\n' "$config" >&2
        exit 1
    fi
    if ((enable_timer)); then
        /usr/local/bin/vps-backup schedule --config "$config" --mode systemd
    else
        printf '\nScheduling was not changed. Back up now with: vps-backup backup\n'
    fi
elif [[ $role == client ]]; then
    printf 'Setup is still needed. Run: vps-backup setup --config %q\n' "$config"
else
    printf 'Gateway commands are ready. Next: %s setup-server --help\n' "$command_name"
fi
