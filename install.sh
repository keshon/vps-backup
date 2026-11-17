#!/usr/bin/env bash
set -euo pipefail
umask 077

role=client
bundle=''
enable_timer=0
storage_mode=restic
command_name=vps-backup
while (($#)); do
    case "$1" in
        --role) role=${2:?Missing role}; shift 2 ;;
        --bundle) bundle=${2:?Missing enrollment bundle}; shift 2 ;;
        --storage-mode) storage_mode=${2:?Missing storage mode}; shift 2 ;;
        --enable-timer) enable_timer=1; shift ;;
        --command-name) command_name=${2:?Missing command name}; shift 2 ;;
        *) printf 'Usage: bash install.sh [--role client|server] [--bundle /path/to/bundle] [--enable-timer] [--storage-mode restic|archive] [--command-name NAME]\n' >&2; exit 2 ;;
    esac
done
[[ $EUID == 0 ]] || { echo 'Run as root.' >&2; exit 1; }
[[ $role == client || $role == server ]] || { echo 'Unknown role.' >&2; exit 1; }
[[ $command_name =~ ^vps-backup(-[a-z0-9-]+)?$ ]] || { echo 'Invalid command name.' >&2; exit 1; }
[[ $command_name == vps-backup || ( $role == server && -z $bundle && $enable_timer == 0 ) ]] || { echo 'Alternate command names are supported for server-only installs.' >&2; exit 1; }
[[ -z $bundle || $role == client ]] || { echo '--bundle applies only to clients.' >&2; exit 1; }
[[ $enable_timer == 0 || ( $role == client && -n $bundle ) ]] || { echo '--enable-timer requires --bundle on a new client.' >&2; exit 1; }
[[ $storage_mode == restic || $storage_mode == archive ]] || { echo "Unknown storage mode." >&2; exit 1; }
command -v apt-get >/dev/null || { echo 'This installer supports Debian/Ubuntu.' >&2; exit 1; }
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
[[ -f $source_dir/vpsbackup/cli.py ]] || { echo 'Download the complete source directory first.' >&2; exit 1; }
[[ ! -e /usr/local/lib/vps-backup ]] || { echo 'Already installed; refusing to overwrite this version.' >&2; exit 1; }
[[ ! -e /usr/local/bin/$command_name ]] || { echo "/usr/local/bin/$command_name already exists; refusing to overwrite." >&2; exit 1; }
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
if [[ $role == server ]]; then
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
exec python3 -c 'from vpsbackup.cli import main; raise SystemExit(main())' "$@"
EOF
chmod 755 "$wrapper_tmp"
mv -T -- "$stage_dir" /usr/local/lib/vps-backup
stage_dir=''
mv -T -- "$wrapper_tmp" "/usr/local/bin/$command_name"
wrapper_tmp=''
install -d -m 700 /etc/vps-backup /var/lib/vps-backup
if [[ -n $bundle ]]; then
    [[ $role == client ]] || { echo '--bundle applies only to clients.' >&2; exit 1; }
    /usr/local/bin/vps-backup setup-client --bundle "$bundle" --initialize --storage-mode "$storage_mode"
fi
if ((enable_timer)); then
    [[ $role == client && -f /etc/vps-backup/client.json ]] || { echo 'Timer requires a configured client.' >&2; exit 1; }
    /usr/local/bin/vps-backup doctor
    /usr/local/bin/vps-backup schedule --mode systemd
fi
printf 'Installed %s. Timer enabled: %s. Gateway start is a separate step.\n' "$role" "$enable_timer"
