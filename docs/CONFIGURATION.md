# microcloud-ui configuration

Implemented in Phase 1 (`backend/mcui/config.py`). Keep this file in sync with
the code (agents.md rule).

Config is TOML (`config.toml`). `mcui` reads `--config <file>`, else `MCUI_CONFIG`, else
`~/.config/mcui/config.toml` if it exists (for development), else nothing: built-in
defaults, no cluster, data in `./data` (D71). `mcui serve` logs which on start. Any key
can be overridden with an env var `MCUI_<SECTION>__<KEY>`, e.g.
`MCUI_METRICS__PORT=8444`. Env vars win over the file. Unknown keys are errors.
`~` is expanded in paths.

## Example (matches the test cloud)

```toml
[server]
data_dir = "~/.local/share/mcui"   # SQLite DB (users, project grants, sessions, audit); default ./data
secure_cookies = true              # session cookie only over HTTPS; set false for http://localhost dev
session_hours = 12
host = "127.0.0.1"                 # where `mcui serve` listens (--host/--port override)
port = 8000                        # production: 8843, see docs/DEPLOY.md
# tls_cert = "/etc/mcui/certs/ui.crt"   # serve HTTPS directly; set both or neither
# tls_key  = "/etc/mcui/certs/ui.key"   # key: chmod 600
# public_url = "https://mcui.example.org"   # behind a proxy that rewrites Host or drops its port:
#                                          # also accepted as the websocket Origin (DEPLOY.md §10)
# forwarded_allow_ips = "192.0.2.20"        # proxies trusted for X-Forwarded-For (IPs/CIDRs, "*")

[lxd]
endpoints   = ["https://<member1-ip>:8443", "https://<member2-ip>:8443"]  # any members; used for failover
server_cert = "~/.config/mcui/certs/lxd-cluster.crt"   # pinned shared cluster cert
server_name = "node-1"                    # SAN name; we connect by IP
client_cert = "~/.config/mcui/certs/client.crt"
client_key  = "~/.config/mcui/certs/client.key"
timeout_seconds = 10

[metrics]
cert = "~/.config/mcui/certs/metrics.crt"   # --type=metrics cert; defaults to client cert if omitted
key  = "~/.config/mcui/certs/metrics.key"
port = 8443                                 # see "Metrics port" below
interval_seconds = 15                       # min 10 (LXD caches metrics ~8s)
store = "sqlite"
collect = true                              # background collection + history (metrics.db in data_dir)
state_interval_seconds = 60                 # member load/memory, pool usage, host NIC counters
retention_raw_hours = 24
retention_1m_days = 14
retention_1h_days = 365

[ceph]
enabled = true                              # record ceph-mgr prometheus metrics
mgr_endpoints = []                          # empty: http://<each member>:<mgr_port>
mgr_port = 9283
timeout_seconds = 10

[node_exporter]                             # optional host metrics, see docs/HOST-METRICS.md
enabled = true                              # false: never try port 9100
scheme = "https"                            # "http" only on a private lab network
port = 9100
ca = "~/.config/mcui/certs/node-exporter.crt"   # empty: system trust store
# server_name = "node-exporter"             # name to check in the cert instead of the IP
username = "mcui"                           # basic auth; set both or neither
password_file = "~/.config/mcui/node-exporter.pass"   # chmod 600
member_overrides = {}                       # e.g. { "gw-1" = "https://10.0.0.1:9100" }
timeout_seconds = 5

# [server] max_upload_bytes = 68719476736   # largest upload (images, ISOs, backups,
#                                           # instance files); 0: no limit (D90)

[alerts]                                    # see below and docs/ALERTS.md
enabled = true
interval_seconds = 30                       # how often the rules run
retention_days = 90                         # how long cleared alerts are kept
# thresholds = { local_pool = [70, 85] }    # [warning, critical]; others keep the defaults
# disabled = ["host_clock"]                 # rules switched off

[host_devices]                              # host paths and USB devices, see below
# hidden_paths = ["/home", "/root", "/etc"] # unset: the built-in list
browse = false                              # the folder browser (helper containers)
browse_project = "mcui-server"              # the LXD project the helpers live in
# browse_pool = "remote"                    # unset: the default profile's root pool
browse_image = "alpine/edge"                # from the images server
browse_idle_minutes = 10                    # a helper stops after this long unused
```

### `[alerts]`: what is checked and when

Rules and their defaults are in [`ALERTS.md`](ALERTS.md). Thresholds, as `[warning,
critical]` in percent: `ceph_raw` (75, 85), `ceph_pool` (80, 90), `local_pool` (80, 90),
`host_fs` (85, 95), `host_memory` (90, 95), `instance_rootfs` (90, 95), `project_limits`
(90, 98), `ovn_uplink` (80, 95), `network_addresses` (80, 95); `cert_days` is days left
(30, 7). Rule names for `disabled`: `ceph_health`, `ceph_raw`, `ceph_pool`, `local_pool`,
`host_fs`, `host_memory`, `host_clock`, `member_status`, `lxd_security`, `instance_oom`,
`instance_rootfs`, `project_limits`, `ovn_uplink`, `network_addresses`, `cert_expiry`,
`collection`. A cluster admin can change both on **Settings → Alerts**; values saved there
win.

### `[host_devices]`: what project admins can find and attach (D87)

A cluster admin can set both of these in the app too, on **Settings**. A value saved there
wins over the file, and **Use the config file** goes back to it, so either way works.

- **`hidden_paths`**: folders that project admins with the host-devices right (D86) can't
  see in the folder browser or the mounted-filesystem list, and can't attach a path inside.
  Cluster admins aren't limited. Unset, the built-in list hides `/home`, `/root`, `/etc`,
  `/snap`, `/var/snap` (LXD, MicroCeph and MicroOVN keys), `/run` and `/var/run` (the LXD
  socket: mounting it hands over the cluster), `/var/lib`, `/var/log`, `/proc`, `/sys`,
  `/dev`, `/boot`, `/tmp`, `/var/tmp`, and the programs and libraries (`/bin`, `/sbin`,
  `/lib*`, `/usr`), `/var/spool` (cron), `/var/cache`, `/var/backups` and `/var/mail`,
  where a writable mount could run code as root. USB disks (`/dev/disk/by-id/usb-…`) are
  always offered. It covers disks, unix devices and a proxy's unix sockets, and a folder
  containing a hidden one (`/`, `/var`) counts as hidden.
  The check is on the path as written, and so is LXD's own
  `restricted.devices.disk.paths` (a prefix match; LXD doesn't resolve symlinks, checked in
  its source). A link inside a folder an instance can write to can point anywhere on the
  member, so allow only folders instances can't write to, or have them attached
  read-only. The folder browser resolves links before its check.
- **`browse`**: off by default. On, the app keeps a small Alpine container per member in
  `browse_project` (created when switched on, marked `user.mcui.role=host-browser`), with
  the member's `/` mounted read-only, and lists folders through it. Helpers start when
  someone browses and stop after `browse_idle_minutes`. Switching it off, in the app or
  here (at the next start), deletes the helpers, their image and the project. The app
  never touches a project of that name without its marker. See
  [`WORKFLOWS.md`](WORKFLOWS.md#find-a-path-on-the-member).

History lives in `<data_dir>/metrics.db`. Expect roughly 0.5–0.7 GB a year
for a three-member cluster with ~10 instances and Ceph at the defaults; see
D36 in agents.md. `GET /api/metrics/status` shows each collection job's last
run, error and sample count, and the store size.

## Validation (CF-1)

The config is checked at startup (`mcui serve`) and by `mcui check`:

- `endpoints` must be `https://` URLs; at least one.
- `server_cert` and `client_cert` must exist.
- Private keys (`client_key`, `[metrics] key`) must exist and must **not** be
  accessible by group or other (like OpenSSH). Fix with `chmod 600 <key>`.
- `[metrics] cert` and `key` are set together or not at all; likewise
  `[server] tls_cert` and `tls_key` (the key must be 0600 too).

Without an `[lxd]` section the server still starts (login, users, audit work)
and LXD pages report "LXD is not configured".

## Checking the setup

```bash
mcui -c config.toml check
```

reports, per configured endpoint, whether it answered, the LXD version and
whether our certificate is trusted; then the cluster members; then a metrics
scrape of every member (port used, sample count, or the error such as an
untrusted metrics cert); then which UI features the server's `api_extensions`
allow. Exit code 0 means everything needed works.

## Metrics port (tool side)

Cluster members are discovered from `GET /1.0/cluster/members`. Each member's
metrics URL is built from that member's address plus the metrics port:

| Setting | Meaning |
|---|---|
| `port = 8443` (default) | Scrape `/1.0/metrics` on the normal API port. Works with no LXD changes. **Current test cloud setting.** |
| `port = 8444` | Scrape the dedicated listener created with `core.metrics_address` (see `docs/LXD-CREDENTIALS.md` §5). Must be enabled on **every** member. |
| `[metrics.member_overrides]` | Optional per-member URL, for members that differ, e.g. `node-2 = "https://10.0.0.12:9444"` |
| `fallback_to_api_port = true` | If the metrics port refuses connections, retry on 8443 and raise a UI warning (default `false`) |

The same `server_cert` and `server_name` are used for verification on both
ports. `mcui check` reports, for each member, which port answered, whether the
certificate matched, and whether the metrics cert was accepted.

### Switching from 8443 to 8444
1. Enable `core.metrics_address` on every member (LXD-CREDENTIALS §5).
2. Run `curl … --resolve "node-1:8444:<ip>" https://node-1:8444/1.0/metrics` for each member.
3. Set `port = 8444` (or `MCUI_METRICS__PORT=8444`) and restart microcloud-ui.
4. Run `mcui check`; all members should show `metrics: ok (8444)`.
