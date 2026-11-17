import base64
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
from urllib.parse import urlsplit

from .common import BackupError, ID, absolute, atomic_json, atomic_text, execute, locked, terminate


def cloud_space(config):
    rclone = config.get("rclone_binary") or shutil.which("rclone")
    if not rclone:
        raise BackupError("Install rclone on the backup server")
    env = {key: value for key, value in os.environ.items() if not key.startswith("RCLONE_")}
    raw = execute([rclone, "--config", config["rclone_config"], "about", config["remote"], "--json"],
                  env=env, timeout=60)
    try:
        reported = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise BackupError("Invalid cloud capacity response") from exc
    if not isinstance(reported, dict):
        raise BackupError("Invalid cloud capacity response")
    result = {"status": "success", "operation": "cloud-space"}
    for field in ("total", "used", "free"):
        value = reported.get(field)
        if value is not None:
            if type(value) is not int or value < 0:
                raise BackupError("Invalid cloud capacity value")
            result[field + "_bytes"] = value
    if not any(field + "_bytes" in result for field in ("total", "used", "free")):
        raise BackupError("Cloud provider did not report capacity")
    return result


def password_entry(username, password):
    return execute(["htpasswd", "-niB", username], input_data=(password + "\n").encode(), timeout=30).decode()


def gateway_args(config):
    rclone = config.get("rclone_binary") or shutil.which("rclone")
    if not rclone:
        raise BackupError("Install rclone on the backup server")
    actual = hashlib.sha256(Path(rclone).read_bytes()).hexdigest()
    if actual != config.get("audited_rclone_sha256"):
        raise BackupError("Rclone binary changed or is untested; run audit-gateway and setup-server again")
    args = [rclone, "--config", config["rclone_config"], "serve", "restic", config["remote"],
            "--addr", config["listen"], "--htpasswd", config["htpasswd"], "--private-repos", "--append-only",
            "--cache-objects=false", "--buffer-size", "4M", "--transfers", "2", "--checkers", "2"]
    if config.get("tls_cert") and config.get("tls_key"):
        args += ["--cert", config["tls_cert"], "--key", config["tls_key"], "--min-tls-version", "tls1.2"]
    else:
        host = config["listen"].rsplit(":", 1)[0].strip("[]")
        try:
            address = ipaddress.ip_address(host)
        except ValueError as exc:
            raise BackupError("Listen address must be an explicit IP") from exc
        trusted_bridge = (config.get("trusted_proxy") is True and address.is_private
                          and not address.is_unspecified and not address.is_link_local)
        if not address.is_loopback and not trusted_bridge:
            raise BackupError("Non-loopback HTTP needs --trusted-proxy on a private IP, or TLS")
    return args


def audit_gateway(executable=None):
    """Exercise the installed binary against disposable local storage, never cloud data."""
    executable = executable or shutil.which("rclone")
    if not executable:
        raise BackupError("Install rclone")
    with tempfile.TemporaryDirectory(prefix="vps-gateway-audit-") as temp:
        root = Path(temp)
        htpasswd = root / "htpasswd"
        password = secrets.token_urlsafe(24)
        atomic_text(htpasswd, password_entry("alice", password) + password_entry("bob", secrets.token_urlsafe(24)))
        config = root / "rclone.conf"
        atomic_text(config, "[local]\ntype = local\n")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        args = [executable, "--config", str(config), "serve", "restic", "local:" + str(root / "storage"),
                "--addr", f"127.0.0.1:{port}", "--htpasswd", str(htpasswd),
                "--append-only", "--private-repos", "--cache-objects=false"]
        env = {k: v for k, v in os.environ.items() if not k.startswith("RCLONE_")}
        with subprocess.Popen(args, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              start_new_session=os.name == "posix") as process:
            try:
                auth = "Basic " + base64.b64encode(("alice:" + password).encode()).decode()
                def request(method, path, data=None, authenticated=True):
                    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                    try:
                        headers = {"Authorization": auth} if authenticated else {}
                        conn.request(method, path, body=data, headers=headers)
                        response = conn.getresponse()
                        body = response.read()
                        return response.status, body
                    finally:
                        conn.close()
                for _ in range(100):
                    if process.poll() is not None:
                        raise BackupError("Gateway audit server exited")
                    try:
                        request("GET", "/alice/config")
                        break
                    except OSError:
                        time.sleep(0.05)
                else:
                    raise BackupError("Gateway audit server did not start")
                def expect(condition, reason):
                    if not condition:
                        raise BackupError("Gateway safety audit failed: " + reason + "; upgrade rclone before deployment")
                expect(request("GET", "/alice/config", authenticated=False)[0] == 401, "authentication")
                expect(request("POST", "/bob/config", b"foreign")[0] in (400, 403, 404), "repository isolation")
                expect(request("POST", "/alice/config", b"original")[0] in (200, 201, 204), "initial write")
                expect(request("POST", "/alice/config", b"replacement")[0] in (400, 403, 409), "overwrite protection")
                expect(request("DELETE", "/alice/config")[0] == 403, "deletion protection")
                expect(request("GET", "/alice/config")[1] == b"original", "original bytes preserved")
                for path in ("/alice/../bob/config", "/alice/%2e%2e/bob/config", "/alice/keys/../../bob/config"):
                    expect(request("POST", path, b"escape")[0] in (400, 403, 404), "path traversal")
                expect(request("POST", "/alice/locks/test", b"lock")[0] in (200, 201, 204), "lock creation")
                expect(request("DELETE", "/alice/locks/test")[0] in (200, 204), "lock release")
                return {"status": "success", "rclone_sha256": hashlib.sha256(Path(executable).read_bytes()).hexdigest()}
            finally:
                terminate(process)


def setup(path, remote, rclone_config, public_url, listen, tls_cert=None, tls_key=None, rclone_binary=None, trusted_proxy=False):
    path = Path(path)
    if path.exists():
        raise BackupError("Server config exists; refusing to overwrite")
    rclone_config = absolute(rclone_config, "rclone_config")
    if not rclone_config.is_file() or (os.name == "posix" and rclone_config.stat().st_mode & 0o077):
        raise BackupError("rclone config must exist with permissions 600")
    import re
    if not re.fullmatch(r"[A-Za-z0-9_-]+:[^\r\n]+", remote) or ".." in remote.split("/"):
        raise BackupError("Use a named rclone remote and a new nonempty backup prefix")
    url = urlsplit(public_url)
    if url.scheme != "https" and not (url.scheme == "http" and url.hostname in ("localhost", "127.0.0.1", "::1")):
        raise BackupError("Public URL requires HTTPS")
    if not url.hostname or url.username or url.password or url.query or url.fragment:
        raise BackupError("Invalid public URL")
    executable = rclone_binary or shutil.which("rclone")
    audit = audit_gateway(executable)
    config = {"version": 1, "remote": remote, "rclone_config": str(rclone_config), "public_url": public_url.rstrip("/") + "/",
              "listen": listen, "htpasswd": str(path.parent / "htpasswd"), "clients_dir": str(path.parent / "clients"),
              "audited_rclone_sha256": audit["rclone_sha256"], "rclone_binary": executable,
              "tls_cert": tls_cert, "tls_key": tls_key, "trusted_proxy": trusted_proxy}
    gateway_args(config)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with locked(path.parent / "admin.lock"):
        if path.exists() or Path(config["htpasswd"]).exists():
            raise BackupError("Existing server configuration; refusing to overwrite")
        atomic_text(config["htpasswd"], "")
        atomic_json(path, config)
    return {"status": "success", "operation": "setup-server", "config": str(path)}


def enroll(config, server_id):
    if any(not isinstance(config.get(field), str) or not config[field]
           for field in ("clients_dir", "htpasswd", "public_url")):
        raise BackupError("Enrollment needs the backup SERVER configuration, not a client recovery key.\n"
                          "Run on the backup server:\n"
                          "  vps-backup enroll --config /etc/vps-backup/server.json --id " + server_id)
    if not ID.fullmatch(server_id):
        raise BackupError("Invalid server ID")
    folder = Path(config["clients_dir"])
    bundle_path = folder / (server_id + ".bundle.json")
    with locked(folder.parent / "admin.lock"):
        if bundle_path.exists():
            raise BackupError("Client exists; refusing to replace its recovery credentials")
        htpasswd = Path(config["htpasswd"])
        entries = htpasswd.read_text()
        if any(line.startswith(server_id + ":") for line in entries.splitlines()):
            raise BackupError("Username exists; refusing to replace its password")
        auth_password = secrets.token_urlsafe(32)
        bundle = {"version": 1, "server_id": server_id,
                  "repository": "rest:" + config["public_url"] + server_id + "/",
                  "rest_username": server_id, "rest_password": auth_password,
                  "repository_password": secrets.token_urlsafe(48),
                  "state_dir": "/var/lib/vps-backup", "reserve_mib": 512, "apps": []}
        # Recovery bundle first: an interruption cannot silently lose the encryption key.
        atomic_json(bundle_path, bundle)
        atomic_text(htpasswd, entries + password_entry(server_id, auth_password))
    return {"status": "success", "operation": "enroll", "bundle": str(bundle_path), "server_id": server_id}


def serve(config):
    args = gateway_args(config)
    env = {key: value for key, value in os.environ.items() if not key.startswith("RCLONE_")}
    os.execvpe(args[0], args, env)
