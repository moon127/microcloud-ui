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
counters) need host access → optional agent or ovn-exporter (Phase 6).

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
**Phase 0: Scaffold. ✅ Done 2026-11-05.** pyproject (FastAPI, httpx, pydantic-settings,
pytest, respx, ruff, mypy), `frontend/` (Vite, React, TS, Vitest), Makefile,
coverage gates, health endpoint, README setup including the macOS dev run (NF-7).

**Phase 1: Connectivity & live metrics. ✅ Done 2026-11-05 (exit test passed against the test cloud).** Config + mTLS client with
pinned server cert (CF-1/2, NF-3), `mcui check`, `api_extensions` feature
flags (CF-3), Prometheus text parser, live per-instance metrics from all
members (MO-1). Local auth + roles + audit log skeleton (AU-1/2/3).
*Exit:* against your test cloud, list members and live metrics.

**Checkpoint after Phase 1: ✅ done 2026-11-05.** The owner enabled the Ceph perf-counter and RBD stats settings (MO-7a, D23, `docs/CEPH-METRICS.md` §2); the OSD latency and per-RBD-image series were confirmed and recorded for the Phase 4 views, and the settings are kept on.

**Phase 2: Metrics history & dashboards.** ✅ Built 2026-11-05 (2a: collector, store, query API; 2b: pages and charts). Background scraper, SQLite
store with rollups, query API with rate maths (MO-2). Cluster overview,
instance and member graphs, top-N (CL-1/2, MO-3/4/5). Storage pool capacity
history (MO-6), network counters (MO-8).

**Phase 3: Instances, profiles, images, projects. ✅ Built 2026-11-05** (owner live checks: `docs/LIVE-TESTS.md`). IN-1…12 (incl. VGA console IN-9 and file browser IN-11),
PR-1, IM-1/2, PJ-1, operations/warnings/events (CL-7/8), evacuate/restore (CL-3).
Every mutation: admin role + CSRF, audited with the LXD operation id (AU-3).
Sub-phases, one commit each (approved 2026-11-05):
- **3a Foundations:** LXD operation helper (start/wait/cancel/progress); `/api/events`
  websocket relaying LXD events (cookie auth + Origin check); operations page with
  cancel (CL-8); warnings list/acknowledge/delete (CL-7); member evacuate/restore
  (CL-3); project switcher, confirm dialogs, form+YAML editor, action toasts.
- **3b Instances:** list filters + bulk lifecycle (IN-1, IN-3); instance tabs for
  config/devices (IN-4), snapshots (IN-5), logs (IN-10); rename/copy/move (IN-6),
  publish as image (IN-12).
- **3c Create instance (IN-2):** local or remote (simplestreams) image picker,
  target, profiles, limits, root disk, NICs, disks, cloud-init, security, boot.
- **3d Consoles:** exec terminal (IN-7, xterm.js), text console + log (IN-8),
  VGA console (IN-9, spice-html5 LGPL module) — backend proxies LXD websockets.
- **3e File browser (IN-11).**
- **3f Profiles (PR-1), images (IM-1/2), projects (PJ-1).**
Live tests that change things run only inside the `mcui-test` sandbox project (D41).

**Phase 4: Networking & storage management + Ceph/OVN views. ✅ Built 2026-11-11** (owner live checks: `docs/LIVE-TESTS.md`). Includes local pool support verified against recorded zfs/dir/lvm/btrfs fixtures (the test cloud is Ceph-only). NW-1/2/4/5,
ST-1/2/3, Ceph dashboard (MO-7) incl. OSD latency + per-RBD-image I/O built against fixtures (MO-7a), OVN view from LXD API (MO-9).
Sub-phases, one commit each (approved 2026-11-11):
- **4a Networks (NW-1/2):** list/detail per member, used-by, leases, allocations; create/edit/delete bridge, OVN, physical, macvlan.
- **4b ACLs and forwards (NW-4 CRUD, NW-5).**
- **4c Storage pools (ST-1/2):** detail with per-member state and capacity history; create/edit/delete zfs, dir, lvm, btrfs, ceph, cephfs.
- **4d Volumes (ST-3):** custom volumes, resize, attach/detach, snapshots and schedules.
- **4e Ceph dashboard (MO-7, MO-7a).**
- **4f OVN view (MO-9).**
- **4g Hosts (MO-4, MO-12):** added 2026-11-11 at the owner's request. Overview Hosts table (CPU, memory, swap, disk, allocation/overcommit, network) and node_exporter scraping with member-page host graphs; brought forward from Phase 5 (D58, `docs/HOST-METRICS.md`).
Live tests (D41): everything inside a throwaway LXD in a `mcui-test` VM, which also supplies the local-pool fixtures; on the real cluster only `mcui-test-*` ACLs, forwards and a bridge network.

### Post-MVP
Renumbered 2026-11-12 (D73): MicroCloud 3.4 LTS support became Phase 5, so the earlier
Phases 5, 6 and 7 are now 6, 7 and 8. Decisions D58 and D63–D65 keep the old numbers.

**Phase 5: MicroCloud 3.4 LTS. Agreed 2026-11-12; starts after the owner's cluster
upgrade.** Make the app fully support MicroCloud 3.4 LTS while still working with
MicroCloud 2.1 LTS, which the test cloud runs today.

*What changes underneath* (release notes read 2026-11-12):

| | MicroCloud 2.1 LTS (test cloud now) | MicroCloud 3.4 LTS (3.4.1 current) |
|---|---|---|
| LXD | 5.21 LTS (5.21.8/5.21.9 on the test cloud); maintenance support to June 2029 | 6.10 LTS (6.10.1 current); full support, then maintenance to June 2031 |
| Ceph | MicroCeph Squid (19.2) | MicroCeph Tentacle (20.2) |
| OVN | MicroOVN 24.03 | MicroOVN 26.03 |
| Snap base | core24 | core26 (needs snapd 2.75 or later) |

The app only talks to the LXD API, the Ceph mgr's Prometheus endpoint and node_exporter,
never to the MicroCloud, MicroCeph or MicroOVN daemons. So the work is mostly about LXD
5.21 → 6.10 and Ceph Squid → Tentacle. A first check of the code (2026-11-12) found no use
of anything LXD 6 removed outright: no `/1.0/containers` or `/1.0/virtual-machines`, no
direct-URL image import, no `nvidia.*` keys, no `security.secure_boot`.

*Support policy (agreed with the owner):* support both MicroCloud 2.x (LXD 5.21) and 3.4
(LXD 6.10), with each feature switched on
by `api_extensions` as now (D19). Both LTS releases are supported upstream until 2029 and
2031. Once the test cloud is upgraded, the only cluster left on 5.21 is the recorded
fixtures, so record a full set **before** the upgrade (5.0).

**5.0 Before and during the upgrade (owner, with help).**
- **Security first:** LXD before 5.21.9 or 6.10.1 has a critical authentication bypass
  in client-certificate checking (advisory of 2026-10-28). Member 1 still runs 5.21.8:
  `sudo snap refresh lxd` there now.
- **Record the 5.21 baseline. ✅ Done 2026-11-12:** `record-fixtures` now covers everything
  the app reads (profiles, images, projects, ACLs, allocations, operations, each network's
  forwards, load balancers, peers, leases and state, the Ceph mgr and node_exporter), and
  `backend/tests/fixtures/recorded-5.21/` holds the result (55 files).
- **Upgrade** following MicroCloud's "Upgrade MicroCloud" guide (2 → 3 track). Caveats
  from the release notes:
  - Squid → Tentacle: an upgrade from Squid 19.2.6 to Tentacle releases before 20.2.4
    may leave the Ceph managers unable to start. Go straight to a current Tentacle.
  - LXD 6.10 lists a known issue: during MicroCeph upgrades, don't rotate keys until
    LXD's Ceph client supports the new key type.
  - snapd 2.75 or later on every member.
- **After:** `mcui check`, the integration tests, and `docs/LIVE-TESTS.md`. This is
  "verify the current code still works"; anything that fails feeds 5a.

**5a Compatibility (must have).** One commit per item, each tested on fixtures from both
LTS releases and checked live on 3.4.
- **Two fixture sets:** `recorded-5.21` (from 5.0) and `recorded-6.10`, with the
  recorded-fixture tests run against both. The demo cluster for screenshots (D59) moves
  to LXD 6.10.
- **Background operations:** LXD 6.6 and 6.8 made create, update, rename and delete
  return background operations for storage pools, volumes, buckets, networks, ACLs,
  forwards, load balancers, peers, zones, and profile PUT/PATCH; project delete followed
  in 6.7. The backend (`operations.start`) and the frontend (`actions.ts`) already follow
  operations, so this is mainly checking every form waits for the result and shows its
  errors.
- **Operation targets:** an operation's `resources` is now empty; the target is in
  `entity_url` (and `original_entity_url` after a rename). The operations page, toasts
  and audit log should use it. Show child operations (`recursion=2`, `child_count`).
- **Renamed and removed settings:**
  - CephFS pools take `cephfs.path`, not `source`: the create-pool form still sends
    `source`. Ceph RBD pools use `ceph.osd.pool_name`, which the form already does.
  - `boot.mode` replaces `security.secure_boot`/`security.csm`, and VM disks use
    virtiofs instead of 9p: hints in the create wizard and the config editor.
  - Gone: `cluster.healing_threshold`, `nvidia.*`, `maas.*` and `fan.type=ipip`.
  - The `event-hub` member role becomes `control-plane`.
- **Evacuate and restore:** LXD 6.9 refuses to evacuate a member when that would break
  the database quorum, unless forced (`clustering_evacuation_force`). Restore conflicts
  with a running evacuation. Show the refusal and offer "force" with a warning
  (fixture-tested; the owner runs it, D41).
- **Metrics:** check that `/1.0/metrics` names still match (none are renamed in the
  release notes). Memory "available" now counts reclaimable slab (6.9), so check the
  memory figures.
- **Ceph Tentacle:** the mgr Prometheus module no longer exports daemon perf counters by
  default; `ceph-exporter` does. So the OSD apply/commit and per-op latency graphs
  (MO-7a) go blank unless `mgr/prometheus/exclude_perf_counters` is set to false (one
  `ceph config set`, owner's call) or we scrape `ceph-exporter`, if MicroCeph runs it.
  Check the RBD image stats and the remaining metric names on the live cluster;
  `docs/CEPH-METRICS.md` follows.
- **`mcui check`:** report each member's LXD version and LTS track, warn about mixed
  versions, and flag LXD releases with known security fixes outstanding (5.21.8 today).
- **Hosts on Ubuntu 26.04** (if the owner upgrades them): node_exporter from 26.04 and
  `docs/HOST-METRICS.md`.

**5b What LXD 6 adds** (each behind its `api_extensions` flag). The owner chose
(2026-11-12) placement groups, image registries, OVN load balancers, child operations
and multi-volume snapshots for Phase 5:
- **Placement groups** (`instance_placement_groups`): list, create, edit, and use them in
  the create-instance target.
- **Image registries** (`image_registries`): manage them, and use them in the image
  picker, replacing or alongside our own remote catalogues (D47).
  `restricted.registries` per project.
- **OVN:** load-balancer pools and health checks (`network_load_balancer_pool*`, from
  NW-4/7), internal load balancers and forwards, DHCP ranges, automatic IP allocation
  for forwards and load balancers.
- **Child operations:** parent and child operations on the operations page and in
  toasts (`recursion=2`, `child_count`); bulk and durable operations shown as they come.
- **Multi-volume snapshots** (`instance_snapshots_multi_volume`): snapshot an instance
  with its attached volumes, and show which volumes a snapshot holds.

Not in Phase 5:
- **Disaster recovery** (cluster links, replicators, project replica mode): a later
  stage. It needs a second cluster.
- **Identities, OIDC and bearer tokens** (AC-1): waiting for a separate discussion. The
  app talks to LXD as one admin client; whether it should act with each user's or
  project's own credentials instead is a design question to settle first.
- Forced delete, per-project images and backups volumes, GPU via CDI: small; pick up
  in Phase 7 or when needed.

**5c MicroCloud itself (evaluate, then decide).** MicroCloud 3.4 has a generally available
Cluster Manager with its own API, and an observability charm for COS/Grafana. Decide
whether the app should show MicroCloud-level facts (component versions, Cluster Manager
status) or leave them to those tools.

*Exit:* every page and action works against the upgraded 3.4 test cloud (`LIVE-TESTS.md`
updated and run), the recorded-fixture suites pass for both LTS releases, the 5b items
agreed with the owner are built, and README, DEPLOY.md and CEPH-METRICS.md say which
MicroCloud releases are supported.

*Decided 2026-11-12:* keep supporting MicroCloud 2.x; the 5b scope above; 5.21 fixtures
recorded.

*Still open:*
1. Ceph perf counters on Tentacle: set `exclude_perf_counters=false` on the cluster, or
   should we scrape `ceph-exporter`? (Check what MicroCeph runs after the upgrade.)
2. The hosts' own Ubuntu: MicroCloud 3.4 asks for an Ubuntu LTS from 22.04 up with
   snapd 2.75 or later, so the members can stay on 24.04. Moving them to 26.04 is the
   owner's choice; it would change node_exporter and the kernel under LXD.

**Phase 6: Metric store evaluation** (was Phase 5; MO-14): benchmark SQLite against
Prometheus, VictoriaMetrics and others (e.g. TimescaleDB, InfluxDB, ClickHouse)
on ingest, query latency, disk use and ops burden; implement the chosen
adapter. Alerts (MO-13), export (MO-15), project quota
history (MO-10).

**Phase 7: Remaining parity** (was Phase 6). Backups (IN-13, ST-4), project moves and
restrictions (IN-6, PJ-2), ISO upload (IM-3), sriov, peers, zones, ACL logs
(NW-3/4/6/7), cluster groups and config (CL-4/5). Login options evaluation (AU-4), with
identities (AC-1) once the auth model is agreed. OVN load balancers moved to Phase 5b.

**Phase 8: Packaging** (was Phase 7). Wheel + systemd unit, OCI image, docs for running
on the MicroCloud itself as an LXD instance (NF-6).
- 8a (was 7a) ✅ 2026-11-12 (brought forward): Ubuntu + systemd. `make bundle` (wheel with the SPA
  plus dependency wheels, offline), `deploy/install.sh` (install/upgrade/rollback/uninstall),
  hardened unit, `mcui` admin wrapper, built-in HTTPS (`[server] tls_cert/tls_key`),
  `docs/DEPLOY.md` (D63). Install, upgrade, rollback and uninstall verified in an Ubuntu 24.04 VM
  on the test cloud.
- 8b (was 7b) ✅ 2026-11-12: OCI image (`deploy/container/Containerfile`, `make image`/`image-save`),
  Podman Quadlet (recommended, host network) and Compose (not on members), databases 0600
  regardless of umask; DEPLOY.md §9 (D64). Verified in an Ubuntu 24.04 VM with Podman 4.9 and
  Docker 29. Published to ghcr.io (multi-arch) with releases and edge builds (D67, D68).
- 8c (was 7c) ✅ 2026-11-12: behind a reverse proxy (DEPLOY.md §10: NGINX Proxy Manager, Cosmos,
  Caddy, nginx); `[server] public_url` and `forwarded_allow_ips` (D65). Tested live in an
  Ubuntu 24.04 VM against NPM 2.17.0, Cosmos and Caddy 2.6.2.
- 8d ✅ 2026-11-12: Ubuntu 26.04 bundles and image (D72).

**Later:** OVN/OVS node agent (MO-11), buckets, LXD auth management beyond identities.

## 4. Test strategy
- Unit: parser, rollups, rate maths (incl. counter resets), config, auth.
- API: FastAPI `AsyncClient` with LXD mocked by `respx` from recorded,
  scrubbed fixtures (a fixture recorder script will capture them from your test
  cluster once you share access).
- WebSocket proxy tests with an in-process fake LXD websocket.
- Frontend: Vitest + Testing Library (≥90%); Playwright e2e against a mocked backend.
- Opt-in integration suite against a real MicroCloud (env-var gated).
- **Local storage:** covered by fixtures recorded from a throwaway LXD with zfs,
  btrfs, lvm and dir pools (`backend/tests/fixtures/recorded-local`, 2026-11-11);
  remaining caveats in agents.md "Known gaps".

## 5. What I need from you
See open questions in `docs/REQUIREMENTS.md` §7, then the test-cloud IPs and certs:
member IPs/ports, a trusted client cert+key, a `--type=metrics` cert+key, the cluster
`server.crt`, and Ceph mgr hosts if the prometheus module is enabled.
