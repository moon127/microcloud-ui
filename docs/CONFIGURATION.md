# microcloud-ui configuration

Implemented in Phase 1 (`backend/mcui/config.py`). Keep this file in sync with
the code (agents.md rule).

Config is TOML (`config.toml`, path via `mcui --config <file>` or `MCUI_CONFIG`). Any key
can be overridden with an env var `MCUI_<SECTION>__<KEY>`, e.g.
`MCUI_METRICS__PORT=8444`. Env vars win over the file. Unknown keys are errors.
`~` is expanded in paths.

## Example (matches the test cloud)

```toml
[server]
data_dir = "~/.local/share/mcui"   # SQLite DB (users, sessions, audit); default ./data
secure_cookies = true              # session cookie only over HTTPS; set false for http://localhost dev
session_hours = 12

[lxd]
endpoints   = ["https://<member1-ip>:8443", "https://<member2-ip>:8443"]  # any members; used for failover
server_cert = "~/.config/mcui/certs/lxd-cluster.crt"   # pinned shared cluster cert
server_name = "gw-ips-microcloud-1"                    # SAN name; we connect by IP
client_cert = "~/.config/mcui/certs/client.crt"
client_key  = "~/.config/mcui/certs/client.key"
timeout_seconds = 10

[metrics]
cert = "~/.config/mcui/certs/metrics.crt"   # --type=metrics cert; defaults to client cert if omitted
key  = "~/.config/mcui/certs/metrics.key"
port = 8443                                 # see "Metrics port" below
interval_seconds = 15                       # min 10 (LXD caches metrics ~8s)
store = "sqlite"
```

## Validation (CF-1)

The config is checked at startup (`mcui serve`) and by `mcui check`:

- `endpoints` must be `https://` URLs; at least one.
- `server_cert` and `client_cert` must exist.
- Private keys (`client_key`, `[metrics] key`) must exist and must **not** be
  accessible by group or other (like OpenSSH). Fix with `chmod 600 <key>`.
- `[metrics] cert` and `key` are set together or not at all.

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
| `[metrics.member_overrides]` | Optional per-member URL, for members that differ, e.g. `gw-ips-microcloud-2 = "https://10.0.0.12:9444"` |
| `fallback_to_api_port = true` | If the metrics port refuses connections, retry on 8443 and raise a UI warning (default `false`) |

The same `server_cert` and `server_name` are used for verification on both
ports. `mcui check` reports, for each member, which port answered, whether the
certificate matched, and whether the metrics cert was accepted.

### Switching from 8443 to 8444
1. Enable `core.metrics_address` on every member (LXD-CREDENTIALS §5).
2. Run `curl … --resolve "gw-ips-microcloud-1:8444:<ip>" https://gw-ips-microcloud-1:8444/1.0/metrics` for each member.
3. Set `port = 8444` (or `MCUI_METRICS__PORT=8444`) and restart microcloud-ui.
4. Run `mcui check`; all members should show `metrics: ok (8444)`.
