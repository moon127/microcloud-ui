# microcloud-ui — Delivery plan

A self-hosted web UI for Canonical MicroCloud (LXD + MicroCeph + MicroOVN) that
covers the day-to-day functionality of the stock LXD/MicroCloud UI **and** adds
the missing observability: per-instance CPU/memory/disk/network history, Ceph
and local storage capacity/performance, and OVN network internals.

See `agents.md` for the design-decision log, `docs/REQUIREMENTS.md` for requirement
IDs/tiers, and `docs/lxd-api-surface.md` for the LXD REST reference.

## 1. What the research found

### LXD metrics (`/1.0/metrics`)
- Prometheus text format. Served on `core.https_address` (full API) or a
  dedicated metrics-only listener `core.metrics_address` (e.g. `:8444`).
- Auth: mTLS with a client cert. Recommended: a dedicated metrics cert,
  `lxc config trust add metrics.crt --type=metrics` (can only read metrics).
  `core.metrics_authentication=false` exists but is not recommended.
- Client verifies LXD against the cluster's shared `cluster.crt`
  (`/var/snap/lxd/common/lxd/cluster.crt`, identical on all members; standalone
  servers use `server.crt`), with a `server_name` override when the SAN
  doesn't list the IP. See `docs/LXD-CREDENTIALS.md`.
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
operations. It is a static SPA served by LXD at `/ui` that calls the
public REST API directly, so full parity is achievable from our backend.
Its only metrics are **live**: it polls `/1.0/metrics` every 15s and keeps
the last 6 samples (~90s) in browser memory (instance CPU/memory/root FS,
member CPU/memory). It keeps no history and has no disk-I/O, network,
storage, Ceph or OVN stats. We will not fork it (GPL-3.0, and we want a
Python backend); see `docs/REQUIREMENTS.md` §1.

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
server_cert = "/etc/mcui/lxd-cluster.crt"   # shared cluster.crt (pinned)
server_name = "microcloud"          # optional SAN override
client_cert = "/etc/mcui/client.crt"
client_key  = "/etc/mcui/client.key"

[metrics]
cert = "/etc/mcui/metrics.crt"      # --type=metrics; defaults to client_cert
key  = "/etc/mcui/metrics.key"
port = 8443                         # default; 8444 if core.metrics_address enabled (docs/CONFIGURATION.md)
interval_seconds = 15
store = "sqlite"                    # only option for MVP (D8)

[ceph]
mgr_endpoints = ["http://10.0.0.11:9283", "http://10.0.0.12:9283"]
```

## 3. Phased delivery

Requirement IDs refer to `docs/REQUIREMENTS.md`. Every phase ships tests in
the same commit with coverage ≥90% (backend and frontend), and updates
agents.md with any decision.

### MVP
**Phase 0: Scaffold.** pyproject (FastAPI, httpx, pydantic-settings,
pytest, respx, ruff, mypy), `frontend/` (Vite, React, TS, Vitest), Makefile,
coverage gates, health endpoint, README setup including the macOS dev run (NF-7).

**Phase 1: Connectivity & live metrics.** Config + mTLS client with
pinned server cert (CF-1/2, NF-3), `mcui check`, `api_extensions` feature
flags (CF-3), Prometheus text parser, live per-instance metrics from all
members (MO-1). Local auth + roles + audit log skeleton (AU-1/2/3).
*Exit:* against your test cloud, list members and live metrics.

**Checkpoint after Phase 1:** owner briefly enables the Ceph perf-counter and RBD stats settings (MO-7a, D23, `docs/CEPH-METRICS.md` §2) to validate the already-built OSD latency and per-disk I/O views live, measuring mgr load before and after, then decides whether to keep them on.

**Phase 2: Metrics history & dashboards.** Background scraper, SQLite
store with rollups, query API with rate maths (MO-2). Cluster overview,
instance and member graphs, top-N (CL-1/2, MO-3/4/5). Storage pool capacity
history (MO-6), network counters (MO-8).

**Phase 3: Instances, profiles, images, projects.** IN-1…8, IN-10, IN-12,
PR-1, IM-1/2, PJ-1, operations/warnings/events (CL-7/8), evacuate/restore (CL-3).

**Phase 4: Networking & storage management + Ceph/OVN views.** NW-1/2/4/5,
ST-1/2/3, Ceph dashboard (MO-7) incl. OSD latency + per-RBD-image I/O built against fixtures (MO-7a), OVN view from LXD API (MO-9).

### Post-MVP
**Phase 5: Metric store evaluation** (MO-14): benchmark SQLite against
Prometheus, VictoriaMetrics and others (e.g. TimescaleDB, InfluxDB, ClickHouse)
on ingest, query latency, disk use and ops burden; implement the chosen
adapter. Alerts (MO-13), export (MO-15), node_exporter (MO-12), project quota
history (MO-10).

**Phase 6: Remaining parity.** VGA console (IN-9), file browser (IN-11),
backups (IN-13, ST-4), project moves and restrictions (IN-6, PJ-2), ISO
upload (IM-3), sriov, LBs, peers, zones, ACL logs (NW-3/4/6/7), cluster groups
and config (CL-4/5), identities view (AC-1). Login options evaluation (AU-4).

**Phase 7: Packaging.** Wheel + systemd unit, OCI image, docs for running
on the MicroCloud itself as an LXD instance (NF-6).

**Later:** OVN/OVS node agent (MO-11), cluster links/replicators,
placement groups, image registries, buckets, LXD auth management.

## 4. Test strategy
- Unit: parser, rollups, rate maths (incl. counter resets), config, auth.
- API: FastAPI `AsyncClient` with LXD mocked by `respx` from recorded,
  scrubbed fixtures (a fixture recorder script will capture them from your test
  cluster once you share access).
- WebSocket proxy tests with an in-process fake LXD websocket.
- Frontend: Vitest + Testing Library (≥90%); Playwright e2e against a mocked backend.
- Opt-in integration suite against a real MicroCloud (env-var gated).

## 5. What I need from you
See open questions in `docs/REQUIREMENTS.md` §7, then the test-cloud IPs and certs:
member IPs/ports, a trusted client cert+key, a `--type=metrics` cert+key, the cluster
`server.crt`, and Ceph mgr hosts if the prometheus module is enabled.
