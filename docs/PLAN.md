# microcloud-ui — Delivery plan

A self-hosted web UI for Canonical MicroCloud (LXD + MicroCeph + MicroOVN) that
covers the day-to-day functionality of the stock LXD/MicroCloud UI **and** adds
the missing observability: per-instance CPU/memory/disk/network history, Ceph
and local storage capacity/performance, and OVN network internals.

See `agents.md` for the design-decision log (D1–D15 referenced below).

## 1. What the research found

### LXD metrics (`/1.0/metrics`)
- Prometheus text format. Served on `core.https_address` (full API) or a
  dedicated metrics-only listener `core.metrics_address` (e.g. `:8444`).
- Auth: mTLS with a client cert. Recommended: a dedicated metrics cert,
  `lxc config trust add metrics.crt --type=metrics` (can only read metrics).
  `core.metrics_authentication=false` exists but is not recommended.
- Client needs the server's `server.crt` (from `/var/snap/lxd/common/lxd/`) to
  verify, and a `server_name` override when the SAN doesn't match the IP.
- **Per-member:** each cluster member only reports its own instances → scrape
  every member. Project filter via `?project=`. Results cached ~8s; scraping is
  relatively expensive — 15s default interval is fine.
- Instance metrics (labels `name`, `project`, `type` + per-metric labels):
  `lxd_cpu_seconds_total{cpu,mode}`, `lxd_cpu_effective_total`,
  `lxd_memory_*` (MemTotal/MemAvailable/RSS/Cached/Swap/OOM_kills_total …),
  `lxd_disk_{read,written}_bytes_total{device}`, `lxd_disk_{reads,writes}_completed_total`,
  `lxd_filesystem_{size,avail,free}_bytes{device,fstype}`,
  `lxd_network_{receive,transmit}_{bytes,packets,errs,drop}_total{device}`,
  `lxd_procs_total`.
- Daemon metrics: `lxd_api_requests_completed_total{entity_type,result}`,
  `lxd_api_requests_ongoing`, `lxd_operations_total`, `lxd_warnings_total`,
  `lxd_uptime_seconds`, `lxd_go_*`, replicator metrics (leader only).

### Other LXD API data useful for stats (no Prometheus needed)
- `GET /1.0/instances?recursion=2` — instance state incl. live memory, disk
  usage per device, network counters, process count.
- `GET /1.0/cluster/members/<m>/state` — host load averages, memory, storage pool usage.
- `GET /1.0/resources?target=<m>` — host hardware inventory (CPU, RAM, NICs, disks, GPUs).
- `GET /1.0/storage-pools/<p>/resources?target=<m>` — pool space/inode usage.
- `GET /1.0/networks/<n>/state`, `/1.0/network-allocations`, network
  forwards/load-balancers/peers/zones, `/1.0/network-acls/<acl>/log` (OVN ACL logs).
- `GET /1.0/events` (WebSocket) — lifecycle/operation/log events for live UI updates.
- `/1.0/warnings`, `/1.0/operations`.

### Stock UI (canonical/lxd-ui)
React + TypeScript + Vite SPA, GPL-3.0, v0.22 latest. Covers instances
(create/edit/snapshots/migrate/terminal/VGA console/file browser), profiles,
networks (bridge/OVN/physical, ACLs, forwards, load balancers, peering),
storage pools/volumes/buckets, images, projects, cluster members/groups,
placement groups, replicators, permissions/identities/OIDC, warnings,
operations. **It has no metrics/graphs.** We will not fork it (licence +
we want a Python backend); we re-implement against the same REST API.

### MicroCeph
`ceph mgr module enable prometheus` → metrics on each mgr host `:9283`
(only the active mgr answers; no auth). Gives cluster/pool/OSD/PG health,
capacity, IOPS/throughput, latency.

### MicroOVN
No native Prometheus exporter. `microovn status`, `microovn inspect --format=json`
(health/convergence report), and `microovn.ovn-nbctl` / `ovn-sbctl` /
`ovs-appctl` on nodes. LXD's own API already exposes OVN networks and their
sub-objects. Deeper stats (logical flows, chassis, port bindings, OVS datapath
counters) need host access → optional agent or ovn-exporter (Phase 5).

### Host metrics
LXD metrics are per-instance. For host CPU/disk/NIC history, use LXD member
state (coarse) and optionally scrape `node_exporter` if installed.

## 2. Architecture

```
 Browser (React SPA) ──HTTPS/cookie──►  mcui (FastAPI, Python)
                                         ├─ LXD client (httpx, mTLS, full cert)  ──► LXD :8443 (any member, ?target=)
                                         ├─ WS proxy (exec/console/events)        ──► LXD websockets
                                         ├─ Scraper (asyncio tasks)
                                         │    ├─ LXD /1.0/metrics (metrics cert)  ──► every member :8444/:8443
                                         │    ├─ ceph-mgr :9283 (optional)
                                         │    └─ node_exporter :9100 (optional)
                                         ├─ MetricStore: SQLite (default) | Prometheus query (optional)
                                         └─ Auth: local users → OIDC
```

Key points: BFF holds certs (D4); scrape per member (D7); pluggable store (D8);
rates computed at query time (D9).

### Configuration (sketch)
```toml
[lxd]
endpoints   = ["https://10.0.0.11:8443", "https://10.0.0.12:8443"]  # any member; failover
server_cert = "/etc/mcui/lxd-server.crt"
server_name = "microcloud"          # optional SAN override
client_cert = "/etc/mcui/client.crt"
client_key  = "/etc/mcui/client.key"

[metrics]
cert = "/etc/mcui/metrics.crt"      # --type=metrics; defaults to client_cert
key  = "/etc/mcui/metrics.key"
port = 8444                         # core.metrics_address port; falls back to 8443
interval_seconds = 15
store = "sqlite"                    # or "prometheus"
prometheus_url = ""

[ceph]
mgr_endpoints = ["http://10.0.0.11:9283", "http://10.0.0.12:9283"]
```

## 3. Phased delivery

Every phase: tests in the same commit, coverage ≥90% (backend and frontend),
agents.md updated with any decision.

**Phase 0 — Scaffold (½–1 day)**
pyproject (FastAPI, httpx, pydantic-settings, pytest, respx, ruff, mypy),
`frontend/` Vite+React+TS+Vitest, Makefile (`make test`, `make lint`, `make dev`),
coverage gates, pre-commit hook, health endpoint, README setup.

**Phase 1 — LXD connectivity & metrics client (core)**
- Config loader; mTLS `httpx` client with pinned server cert + `server_name`.
- Connection test endpoint + CLI (`mcui check`) reporting trust type, API
  extensions, cluster members.
- Prometheus text parser (via `prometheus_client.parser`) → typed samples.
- One-shot "live metrics" API: scrape all members, merge, return per-instance
  current CPU%/mem/disk/net.
- *Exit:* against your test MicroCloud, list members and live per-instance metrics.

**Phase 2 — Metrics history & dashboards**
- Background scraper (per member, jitter, backoff, member auto-discovery).
- SQLite store with rollups + retention; query API (range, step, rate).
- Prometheus store adapter (PromQL passthrough for the same queries).
- UI: cluster overview (members, instance counts, top-N by CPU/mem/net),
  instance detail graphs (CPU, memory breakdown, disk I/O per device,
  filesystem usage, network per NIC, OOM kills), member detail.

**Phase 3 — Instance & image management (parity, part 1)**
List/filter/search, create (from image/profile), start/stop/restart/freeze,
edit config & devices, snapshots (create/restore/schedule), migrate/move
between members, delete; images list/import/delete; operations & warnings
views; live updates via `/1.0/events`.
Terminal via xterm.js ↔ exec websocket proxy; text console; VGA console
(spice-html5) as stretch.

**Phase 4 — Storage & networking (parity, part 2) + their stats**
- Storage pools/volumes/buckets CRUD; pool capacity per member (local) and
  Ceph pool usage; volume snapshots.
- Ceph dashboard from ceph-mgr metrics: health, OSD up/in, PG states,
  raw/used capacity, per-pool usage, IOPS/throughput/latency.
- Networks CRUD (bridge, OVN, physical/VLAN, macvlan), ACLs, forwards,
  load balancers, peers, zones; network state + allocations view; OVN ACL
  log viewer; per-NIC traffic aggregated by network.

**Phase 5 — OVN/OVS internals (optional agent)**
Small read-only node agent (Python, same repo) or ovn-exporter that runs
`microovn inspect --format=json`, `ovn-nbctl`/`ovn-sbctl` (logical
switches/routers/ports, chassis, port bindings, NB/SB DB size & leader) and
`ovs-appctl` datapath stats; exposed as Prometheus text so the same scraper
ingests it. Topology view (logical router ↔ switches ↔ instances ↔ uplink).

**Phase 6 — Projects, profiles, cluster, auth hardening**
Projects (with limits/usage graphs), profiles, cluster members/groups
(evacuate/restore), placement groups; OIDC login (e.g. Entra ID); roles
(viewer/operator/admin); audit log; alerting thresholds (optional,
e.g. pool > 85%, OOM kills, member down) with webhook.

**Phase 7 — Packaging**
Single wheel + systemd unit; OCI image; docs for running it as an LXD
container on the MicroCloud itself.

## 4. Test strategy
- Unit: parser, rollups, rate maths (incl. counter resets), config, auth.
- API: FastAPI `AsyncClient` with LXD mocked by `respx` from recorded,
  scrubbed fixtures (a fixture recorder script will capture them from your test
  cluster once you share access).
- WebSocket proxy tests with an in-process fake LXD websocket.
- Frontend: Vitest + Testing Library (≥90%); Playwright e2e against a mocked backend.
- Opt-in integration suite against a real MicroCloud (env-var gated).

## 5. What I need from you (for Phase 1)
1. IP(s) of your MicroCloud members and LXD port(s); whether `core.metrics_address` is set.
2. A client cert/key trusted on the cluster (`lxc config trust add client.crt`)
   — ideally restricted to a test project at first — and a metrics cert
   (`lxc config trust add metrics.crt --type=metrics`).
3. The cluster's `server.crt` (`/var/snap/lxd/common/lxd/server.crt`).
4. Whether MicroCeph's mgr prometheus module is enabled (and which hosts).
5. Network reachability from where the backend will run (your Mac's dev VM?).
