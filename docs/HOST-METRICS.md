# Host metrics with node_exporter

LXD reports only coarse host figures: load averages and memory (total, free,
buffers, shared) per member. microcloud-ui shows those for every host. For real
figures, install Prometheus' `node_exporter` on each host. It adds:

- CPU use by mode (user, system, iowait, steal), not just load.
- Memory used without page cache, swap, and the ZFS ARC.
- Filesystem use, per-disk I/O and busy %.
- Pressure stall information: time tasks spent waiting for CPU, memory or I/O.

Without it, the Hosts table and member pages show the LXD figures, marked as
such. Each host is handled on its own, so installing it on some hosts is fine.

mcui reads `https://<member address>:9100/metrics` from each host every 15 s.
Each member's address comes from its LXD cluster address. History keeps only
what the graphs use, as listed in decision D58 in `agents.md`.

## Install (Ubuntu 24.04/26.04)

Checked with node_exporter 1.7.0 (24.04) and 1.10.2 (26.04): the app reads the same metrics from both.

Install it from apt, not the snap store:

```bash
sudo apt install --no-install-recommends prometheus-node-exporter
```

`--no-install-recommends` skips `prometheus-node-exporter-collectors`, a set of
extra scripts and timers (smartmon, apt and others) that mcui doesn't use.

## Lock it down

node_exporter has no access control of its own by default. Anyone who can reach
port 9100 can read a lot about the host: mounts, disks, interfaces and kernel
version. Use its built-in TLS and basic auth (`--web.config.file`, available in
24.04's 1.7.0). Also bind it to the management address.

**Avoid firewall rules as the main control on MicroCloud hosts:**
- **ufw** blocks forwarded traffic by default, which can break LXD bridges and
  OVN.
- **Ubuntu's stock `/etc/nftables.conf`** starts with `flush ruleset`, so
  restarting `nftables.service` deletes LXD's own rules.

If you want a firewall rule as well, add it in a separate nftables table that
you load yourself, never through the stock file.

### 1. A certificate and password (on one host)

One self-signed certificate can cover every host by listing each management IP.
mcui then pins that single certificate, the same way it pins LXD's cluster
certificate.

```bash
sudo apt install --no-install-recommends apache2-utils   # for htpasswd
sudo mkdir -p /etc/prometheus/node-exporter && cd /etc/prometheus/node-exporter
sudo openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 3650 \
  -keyout node.key -out node.crt -subj "/CN=node-exporter" \
  -addext "subjectAltName=IP:192.0.2.11,IP:192.0.2.12,IP:192.0.2.13"
htpasswd -nBC 12 mcui      # prompts for a password; prints mcui:$2y$12$...
```

Use your own management IPs. When you add a host later, reissue the certificate
with that host's IP added, and replace both files on every host and on the mcui
machine.

### 2. On every host

1. Copy `node.crt` and `node.key` into `/etc/prometheus/node-exporter/`.
2. Restrict the key:
   ```bash
   sudo chown root:prometheus /etc/prometheus/node-exporter/node.key
   sudo chmod 640 /etc/prometheus/node-exporter/node.key
   ```
3. Create `/etc/prometheus/node-exporter/web.yml`:
   ```yaml
   tls_server_config:
     cert_file: /etc/prometheus/node-exporter/node.crt
     key_file: /etc/prometheus/node-exporter/node.key
   basic_auth_users:
     mcui: $2y$12$...the hash from htpasswd...
   ```
4. In `/etc/default/prometheus-node-exporter`, use this host's own management
   address:
   ```
   ARGS="--web.listen-address=192.0.2.11:9100 --web.config.file=/etc/prometheus/node-exporter/web.yml"
   ```
5. Restart and check:
   ```bash
   sudo systemctl restart prometheus-node-exporter
   systemctl status prometheus-node-exporter
   ```

### 3. On the mcui machine

1. Copy `node.crt` to `~/.config/mcui/certs/node-exporter.crt`.
2. Put the plain password in `~/.config/mcui/node-exporter.pass` and run
   `chmod 600` on it. mcui refuses a password file other users can read.
3. Add this to `config.toml`:
   ```toml
   [node_exporter]
   ca = "~/.config/mcui/certs/node-exporter.crt"
   username = "mcui"
   password_file = "~/.config/mcui/node-exporter.pass"
   ```
4. Check:
   ```bash
   curl --cacert ~/.config/mcui/certs/node-exporter.crt -u mcui https://192.0.2.11:9100/metrics | head
   mcui -c ~/.config/mcui/config.toml check     # "Host metrics (node_exporter ...)" section
   ```

`mcui check` reports each host as one of:
- `ok`: node_exporter answered.
- `--`: not reachable, normally because it isn't installed there.
- `WARN`: something is wrong, such as a bad password, a certificate
  mismatch or something else answering on the port.

node_exporter problems never make `mcui check` fail.

### Without TLS (lab only)

Use `scheme = "http"` and leave out `ca`, `username` and `password_file`. Then
bind node_exporter to the management address and keep that network private.

## Configuration reference

See `docs/CONFIGURATION.md`, section `[node_exporter]`. Set `enabled = false` to
stop mcui trying port 9100 at all.
