# Running microcloud-ui in production

There are two supported ways to run it. Both use the same config file and the same paths
(`/etc/mcui`, `/var/lib/mcui`) and serve HTTPS on port 8843.

| | **Ubuntu bundle + systemd** (§2–8) | **Container image** (§9) |
|---|---|---|
| What you copy | one tarball (about 11 MB) | an image (about 185 MB, or a 60 MB saved tarball) |
| Needs on the host | `python3-venv`, `openssl` from Ubuntu | Podman 4.4+ (or Docker on a non-member host) |
| Runs as | systemd unit, user `mcui` | Podman Quadlet (systemd) or Compose, uid 8843 |
| On a MicroCloud member | yes | Podman only, never Docker (§9) |
| Tested on | Ubuntu 24.04 VM | Ubuntu 24.04 VM: Podman 4.9 Quadlet, Docker 29 Compose |

The bundle installs microcloud-ui as a systemd service on Ubuntu 24.04 (or 22.04). It is an
**offline bundle**: one tarball with the program, the web UI and every Python
dependency. The target machine needs only `python3`, `python3-venv` and `openssl`
from Ubuntu, and never contacts PyPI. Design notes: D63 (bundle) and D64 (containers) in
`agents.md`.

For development on a Mac, see the README instead.

## 1. Where to run it

Run **one** copy per cluster. It keeps its users, sessions and metric history in
SQLite and collects metrics in-process.

| Option | Good for | Watch out for |
|---|---|---|
| **On a cluster member** | Nothing extra to run; close to LXD | It shares the member's CPU and memory, and goes down with that member. Don't use ufw or the stock `nftables.conf` there ([HOST-METRICS.md](HOST-METRICS.md#lock-it-down)) |
| **A small Ubuntu VM or host alongside the cluster** | Stays up when a member is down; easy to firewall | One more machine to patch |
| **An Ubuntu instance on the cluster** | No extra hardware; can move between members | It can't show you the cluster if the cluster is what's broken. It needs a route to every member's 8443 |

The steps are the same for all three: inside an instance, run them in the instance.
This applies to both the bundle and the container image.

It needs to reach:

- every member's LXD API on **8443**, or the metrics port if you use one
- the Ceph mgr on **9283** and node_exporter on **9100**, if you use them

Browsers reach it on **8843** (HTTPS) by default. The history grows to roughly
0.5–0.7 GB a year for three members (D36).

## 2. Build the bundle (on your dev machine)

```bash
make install          # once: venv + npm packages
make bundle           # Ubuntu 24.04 on x86_64
# make bundle PYVER=3.10            # Ubuntu 22.04
# make bundle ARCH=aarch64          # ARM servers
```

This writes `dist/mcui-<version>-py3.12-x86_64.tar.gz`, about 11 MB. It holds:

- the wheel, with the built web UI inside
- the dependency wheels for that Python and CPU
- `install.sh`, the systemd unit, the `mcui` admin wrapper, an example config, and this guide

The bundle fixes the exact dependency versions. Rebuild it to pick up updates.

Copy it to the server:
```bash
scp dist/mcui-*-py3.12-x86_64.tar.gz <user>@<server>:/tmp/
```

## 3. Install

On the server:
```bash
sudo apt install python3-venv openssl        # python3 is already there on Ubuntu
cd /tmp && tar xzf mcui-*-py3.12-x86_64.tar.gz && cd mcui-*-py3.12-x86_64
sudo ./install.sh
```

`install.sh` is safe to run again. It stops with a clear message if the bundle was
built for a different Python version. It sets up the following:

| What | Where |
|---|---|
| System user and group `mcui`, uid/gid 8843 like the container image (any free id if 8843 is taken), no login shell | |
| Program: a virtualenv per install; `current` links to the one running | `/opt/mcui/<version>-<timestamp>`, `/opt/mcui/current` |
| Config: written once and never overwritten (root:mcui, 0640) | `/etc/mcui/config.toml` |
| Keys and certificates: owned by `mcui`, keys 0600 | `/etc/mcui/certs/` |
| Data: `mcui.db` (users, sessions, audit) and `metrics.db` (history) | `/var/lib/mcui/` |
| HTTPS certificate: self-signed for the host's names and IPs, made only if missing | `/var/lib/mcui/tls/ui.{crt,key}` |
| systemd unit: enabled, then restarted | `/etc/systemd/system/mcui.service` |
| `mcui` admin command: runs as the `mcui` user with the system config | `/usr/local/bin/mcui` |

It finishes by checking that the service is running. If the service isn't running, it shows the
service status.

## 4. First-time setup

**An admin user:**
```bash
sudo mcui user add admin --role admin        # asks for the password twice
```

**LXD credentials.** Make the client keys on the server, so they never leave it:
```bash
sudo -u mcui bash -c 'cd /etc/mcui/certs &&
  openssl ecparam -name secp384r1 -genkey -noout -out client.key &&
  openssl req -new -x509 -sha384 -days 3650 -key client.key -subj /CN=microcloud-ui -out client.crt &&
  openssl ecparam -name secp384r1 -genkey -noout -out metrics.key &&
  openssl req -new -x509 -sha384 -days 3650 -key metrics.key -subj /CN=microcloud-ui-metrics -out metrics.crt &&
  chmod 600 *.key'
```
Then trust `client.crt` and `metrics.crt` in LXD, and fetch the cluster certificate
as `/etc/mcui/certs/lxd-cluster.crt`. Follow [LXD-CREDENTIALS.md](LXD-CREDENTIALS.md) §2–4,
using the paths above instead of `~/.config/mcui/certs`.

If you install it on a member, you can read the cluster certificate directly:
```bash
sudo install -o mcui -g mcui -m 644 /var/snap/lxd/common/lxd/cluster.crt /etc/mcui/certs/lxd-cluster.crt
```

**Config.** Uncomment `[lxd]` (and `[metrics]`, if you made the metrics pair) in
`/etc/mcui/config.toml`, and fill in the member addresses and `server_name`. Every key is
described in [CONFIGURATION.md](CONFIGURATION.md). Then:
```bash
sudo mcui check                  # LXD reachable, cert trusted, metrics scrape per member
sudo systemctl restart mcui
```

Open `https://<server>:8843/`. Your browser will warn about the self-signed
certificate until you trust it (`/var/lib/mcui/tls/ui.crt`) or replace it (§5).

## 5. HTTPS

The service serves HTTPS itself, using `[server] tls_cert` and `tls_key`. Session cookies
are only sent over HTTPS (`secure_cookies = true`), so plain HTTP won't let you
sign in.

- **Your own certificate:** put it in `/etc/mcui/certs/` (for example `ui.crt` and `ui.key`).
  Point `tls_cert` and `tls_key` at those files, then run `sudo systemctl restart mcui`.
  Keep the key owned by `mcui` with mode 0600, or the service refuses to start. A certificate
  chain goes in the `.crt` file, server certificate first. The self-signed pair in
  `/var/lib/mcui/tls/` is then unused.
- **Port 443:** set `port = 443`, then run `sudo systemctl edit mcui` and add:
  ```ini
  [Service]
  AmbientCapabilities=CAP_NET_BIND_SERVICE
  CapabilityBoundingSet=CAP_NET_BIND_SERVICE
  ```
- **Behind a reverse proxy** (nginx, Caddy, HAProxy): set `host = "127.0.0.1"`,
  drop `tls_cert`/`tls_key`, and keep `secure_cookies = true`. The proxy must:
  - pass the original `Host` header, because the console and terminal websockets are refused
    when the browser's Origin doesn't match it
  - support websocket upgrades on `/api/`

  For nginx:
  ```nginx
  location / {
      proxy_pass http://127.0.0.1:8843;
      proxy_http_version 1.1;
      proxy_set_header Host $http_host;
      proxy_set_header Upgrade $http_upgrade;
      proxy_set_header Connection $connection_upgrade;   # map $http_upgrade in http {}
      proxy_read_timeout 1h;                             # long-lived consoles
      client_max_body_size 0;                            # file uploads to instances
  }
  ```

## 6. Who can reach it

Anyone who signs in as an admin can manage the whole cluster. The server holds a
certificate with full LXD access (D4, D21), so treat the machine like LXD itself:

- Bind to the management network: set `host` to that address, not `0.0.0.0`, when the
  machine has other networks.
- Limit access to admins' networks, using a separate firewall or a dedicated nftables table.
  On cluster members, never use ufw or the stock `nftables.conf`
  ([HOST-METRICS.md](HOST-METRICS.md#lock-it-down)).
- Give people their own accounts (`sudo mcui user add <name>`, `--role viewer` for
  read-only). Every change is recorded in the audit log.

The systemd unit runs the service as the unprivileged `mcui` user, with no capabilities
and a read-only system. It can write only to `/var/lib/mcui`, has no access to home
directories, and has a private `/tmp` and devices. Check it with
`systemd-analyze security mcui`.

## 7. Day to day

```bash
systemctl status mcui
journalctl -u mcui -f                  # logs (requests, collector errors)
sudo mcui check                        # connectivity, certs, metrics, node_exporter
sudo mcui user list | add | passwd | delete
sudoedit /etc/mcui/config.toml && sudo systemctl restart mcui
```

A config error stops the service with exit code 2 and doesn't restart it in a
loop. The reason is in `journalctl -u mcui`, for example a key readable by others: run `chmod 600`.

**Upgrade:** build a new bundle, copy it over, unpack it and run `sudo ./install.sh`.
The new version goes into a new directory, and `current` is switched over.

**Roll back:** `install.sh` prints the previous directory, and the last three installs are kept:
```bash
ls /opt/mcui
sudo ln -sfn /opt/mcui/<previous> /opt/mcui/current && sudo systemctl restart mcui
```
A newer version may have changed the databases, so back up before upgrading (below).

**Backup:** copy `/etc/mcui` (config and keys) and `/var/lib/mcui`.

- `mcui.db` is small and holds the users and the audit log.
- `metrics.db` is only history; you can leave it out.

For a consistent copy without stopping the service:
```bash
sudo apt install sqlite3
sudo -u mcui sqlite3 /var/lib/mcui/mcui.db ".backup /var/lib/mcui/mcui-backup.db"
```
Or run `sudo systemctl stop mcui`, copy the files, then start it again.

**Uninstall:**

- `sudo ./install.sh --uninstall` removes the service and the program, and keeps the config and data.
- `--purge` also removes `/etc/mcui`, `/var/lib/mcui` and the `mcui` user.
- Revoke the certificates in LXD as well ([LXD-CREDENTIALS.md](LXD-CREDENTIALS.md#revoking)).

## 8. Troubleshooting

| Symptom | Cause |
|---|---|
| `this bundle is for Python 3.12 but python3 is 3.10` | Built for 24.04 but installing on 22.04. Run `make bundle PYVER=3.10` |
| Service stopped, journal says `config error: … chmod 600` | A key is readable by group or other, or not owned by `mcui` |
| Browser: certificate warning | The self-signed `ui.crt`. Trust it or install your own (§5) |
| Sign-in does nothing | You're on plain HTTP. Use `https://` (or the proxy's HTTPS) |
| Console or terminal: "cross-origin websocket refused" | A proxy changed the `Host` header (§5) |
| "LXD is not configured" | `[lxd]` is still commented out in the config |
| `mcui check`: certificate not trusted | `client.crt` isn't trusted in LXD yet ([LXD-CREDENTIALS.md](LXD-CREDENTIALS.md) §3) |

## 9. Containers (Podman Quadlet, Docker, Compose)

The image (`deploy/container/Containerfile`) is built in three stages: the web UI with
Node 22, the program with its dependencies in a virtualenv, and an `ubuntu:24.04` runtime
with only `python3`, `openssl` and `ca-certificates` added.

- It runs as **uid/gid 8843** (`mcui`) and needs no capabilities.
- It works with a read-only root filesystem.
- It ships `deploy/config.example.toml` as `/etc/mcui/config.toml` and keeps its data in
  the `/var/lib/mcui` volume.
- On first start it makes a self-signed certificate in `/var/lib/mcui/tls`.
- Its health check calls `/api/health`.

### 9.1 Build and copy the image

On a machine with Podman or Docker and internet access:
```bash
make image                 # mcui:<version> and mcui:latest (ENGINE=docker to choose)
make image-save            # dist/mcui-image-<version>.tar.gz, about 60 MB
scp dist/mcui-image-*.tar.gz <user>@<server>:/tmp/
```

`make image` builds with `--network=host`. Inside an LXD instance on an OVN network
(MTU 1442), a container bridge's default MTU of 1500 stalls HTTPS downloads.

On the server:
```bash
sudo podman load -i /tmp/mcui-image-*.tar.gz          # or: sudo docker load -i …
```
The image isn't published to a registry yet.

### 9.2 Podman Quadlet (recommended; fine on a MicroCloud member)

A Quadlet is a systemd unit that runs the container. Podman 4.4 or newer supports it,
and Ubuntu 24.04 has 4.9. The unit file `deploy/container/mcui.container`:

- uses the **host's network**, so Podman adds no NAT or firewall rules to the host;
  that suits a MicroCloud member
- mounts `/etc/mcui` read-only and the `mcui-data` volume
- runs with a read-only root filesystem, no capabilities and no-new-privileges

```bash
sudo apt install podman
sudo install -d -o root -g 8843 -m 0750 /etc/mcui
sudo install -d -o 8843 -g 8843 -m 0700 /etc/mcui/certs
sudo install -o root -g 8843 -m 0640 deploy/config.example.toml /etc/mcui/config.toml
sudo install -m 0644 deploy/container/mcui.container /etc/containers/systemd/
sudo systemctl daemon-reload && sudo systemctl start mcui
sudo podman exec -it mcui mcui user add admin --role admin
```

The two files are in the repository. They're also in the bundle, as `config.example.toml` and
`container/mcui.container`. The Quadlet starts at boot: its `[Install]` section takes the
place of `systemctl enable`.

- **LXD credentials:** follow §4, but make and own the files as uid 8843:
  `sudo chown 8843:8843 /etc/mcui/certs/*` and `sudo chmod 600 /etc/mcui/certs/*.key`.
  Then fill in `[lxd]`, run `sudo podman exec mcui mcui check` and
  `sudo systemctl restart mcui`.
- **HTTPS:** to give the self-signed certificate the names browsers use, uncomment
  `Environment=MCUI_TLS_SAN=…` in the unit file. That only takes effect before the first
  start, or after deleting `/var/lib/mcui/tls`. To take a copy to trust:
  `sudo podman cp mcui:/var/lib/mcui/tls/ui.crt .`.
  Your own certificate goes in `/etc/mcui/certs` as in §5.
- **Day to day:**
  - `systemctl status mcui` and `journalctl -u mcui`
  - `sudo podman healthcheck run mcui`
  - `sudo podman exec mcui mcui user list`
  
  A config error stops it with exit code 2 and doesn't restart it, as with the bundle.
- **Upgrade:** load the new image, which retags `mcui:latest`, then run
  `sudo systemctl restart mcui`.
- **Roll back:** `sudo podman tag mcui:<old version> mcui:latest`, then restart.
- **Backup:** `/etc/mcui`, plus the volume: stop the service, then
  `sudo podman volume export mcui-data -o mcui-data.tar`.
- **Uninstall:**
  ```bash
  sudo systemctl stop mcui && sudo rm /etc/containers/systemd/mcui.container
  sudo systemctl daemon-reload
  sudo podman volume rm mcui-data && sudo podman rmi mcui:latest
  ```

### 9.3 Docker and Compose (not on a MicroCloud member)

**Don't install Docker on a cluster member.** Docker sets the host's firewall `FORWARD`
policy to `DROP`, which breaks LXD bridges and OVN. On a member, use the bundle or the
Podman Quadlet. On a separate host, `deploy/container/compose.yaml` runs the same container:

- read-only root filesystem, all capabilities dropped, no-new-privileges
- port 8843 published, `./etc-mcui` mounted as `/etc/mcui`, and a `mcui-data` volume

```bash
cd deploy/container
mkdir -p etc-mcui/certs && cp ../config.example.toml etc-mcui/config.toml
sudo chown -R 8843:8843 etc-mcui
MCUI_TLS_SAN="DNS:mcui.example.org" docker compose up -d     # builds the image if needed
docker compose exec mcui mcui user add admin --role admin
```

If the Docker host is itself an LXD instance on an OVN network, the bridge's 1500 MTU
stalls HTTPS to LXD. Set the MTU to 1442, using the commented block at the end of
`compose.yaml`. The plain `docker run` equivalent is the same set of options: `--read-only`,
`--cap-drop ALL`, `--security-opt no-new-privileges`, `-p 8843:8843`, and the two volumes.

### 9.4 Container troubleshooting

| Symptom | Cause |
|---|---|
| `config error: cannot read config` or permission denied on `/etc/mcui` | The files aren't readable by uid 8843. Use the owners in §9.2 |
| Key refused (`chmod 600`) | Keys must be mode 0600 and owned by 8843. Docker or Kubernetes secrets mounted 0444/0644 won't do: mount the file instead |
| Builds or LXD connections hang inside an LXD instance | The bridge MTU is larger than the host's (§9.1, §9.3) |
| Switching between the bundle and the container | Both use uid 8843 and the same `/etc/mcui`, so stop one and start the other. `install.sh` resets the config's group and the owner of `certs/` on every run |
| `podman run` shows no health status | The image was built in OCI format. `make image` uses `--format docker` with Podman |
