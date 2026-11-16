# Put the backup server behind Traefik

Do this once on the backup server. Target VPS only need the public HTTPS URL.

The route sends `https://backup.example.com` to the backup gateway on port
8787. Traefik handles the certificate; the gateway checks each client's login.
The installer does not create this route.

## Before you start

Your Traefik must already serve HTTPS and load a watched directory through its
file provider. For Docker, that directory must be mounted into the container.
Use your existing HTTPS entrypoint and certificate resolver names below.

Create a DNS record for your backup hostname pointing to the backup server.
With Cloudflare, use DNS only for the setup described here.

## Connect Traefik to the gateway

Traefik in Docker reaches a service on the host through the Docker network's
gateway address. In this example that address is `172.19.0.1`. Check your
network's actual address; a container's `127.0.0.1` points to the container.

When creating a new backup-server configuration, include that listen address:

```bash
sudo vps-backup --config /etc/vps-backup/server.json setup-server \
  --remote union-koofr:restic-vps \
  --rclone-config /etc/vps-backup/rclone.conf \
  --public-url https://backup.example.com/ \
  --listen 172.19.0.1:8787 --trusted-proxy
```

If you already created `server.json`, edit its `listen` and `trusted_proxy`
fields instead; `setup-server` refuses to overwrite it. Keep the listener on
the private bridge address, restrict access to the intended proxy, and use
HTTPS for client connections. The proxy-to-gateway hop is local plaintext.

Start the gateway using [the service setup](running.md#backup-server), or
restart it after changing an existing configuration:

```bash
sudo systemctl restart vps-backup-gateway
```

## Install the route file

From the repository directory, copy the example into Traefik's watched host
directory. These commands use the directory from the compose-generator layout;
replace it if your deployment uses another path:

```bash
sudo cp deploy/traefik-backup.yml \
  /opt/dockers/traefik-compose-generator/dynamic/vps-backup.yml
sudo nano /opt/dockers/traefik-compose-generator/dynamic/vps-backup.yml
```

Set the `Host` rule to your hostname. Match `entryPoints` and `certResolver` to
your existing Traefik setup. Set the backend URL to the gateway's listen
address, here `http://172.19.0.1:8787`.

With file-provider watching enabled, Traefik loads the file automatically.
The example's backend timeouts do not change Traefik's incoming request
timeout; allow enough time there for slow backup uploads too.

## Check the route

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://backup.example.com/
```

Expect `401`: HTTPS works and the gateway requires a login. A `404` usually
means the route did not match; `502` means Traefik cannot reach the gateway.
After this check, continue with [adding a VPS](setup.md#add-another-vps).
