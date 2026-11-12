# Running microcloud-ui in production (Ubuntu + systemd)

This guide installs microcloud-ui as a systemd service on Ubuntu 24.04 (or 22.04) from an
**offline bundle**: one tarball with the program, the web UI and every Python
dependency. The target machine needs only `python3`, `python3-venv` and `openssl`
from Ubuntu, and never contacts PyPI. Design notes: D63 in `agents.md`.

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
| System user and group `mcui` (no login shell) | |
| Program: a virtualenv per install; `current` links to the one running | `/opt/mcui/<version>-<timestamp>`, `/opt/mcui/current` |
| Config: written once and never overwritten (root:mcui, 0640) | `/etc/mcui/config.toml` |
| Keys and certificates: owned by `mcui`, keys 0600 | `/etc/mcui/certs/` |
| Data: `mcui.db` (users, sessions, audit) and `metrics.db` (history) | `/var/lib/mcui/` |
| HTTPS certificate: self-signed for the host's names and IPs, made only if missing | `/etc/mcui/certs/ui.{crt,key}` |
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
certificate until you trust it (`/etc/mcui/certs/ui.crt`) or replace it (§5).

## 5. HTTPS

The service serves HTTPS itself, using `[server] tls_cert` and `tls_key`. Session cookies
are only sent over HTTPS (`secure_cookies = true`), so plain HTTP won't let you
sign in.

- **Your own certificate:** copy it over the self-signed pair. Keep the key owned
  by `mcui` with mode 0600, or the service refuses to start. Then run
  `sudo systemctl restart mcui`. A certificate chain goes in `ui.crt`, server
  certificate first.
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
