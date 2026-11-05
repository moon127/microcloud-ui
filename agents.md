# agents.md — microcloud-ui

Guidance for humans and AI agents working in this repo, plus the running log of
design decisions. **Update this file in the same commit as any change that
makes or alters a design decision.**

## Ground rules

- **Commit identity:** every commit is authored *and* committed as
  `gareth@woolridge.org.uk` (set via repo-local `git config user.email`; never
  rely on global config).
- **Tests with every feature:** no functionality lands without tests in the
  same commit. Run the full suite before committing.
- **Coverage floor: 90%** line+branch, enforced separately for backend
  (`pytest --cov --cov-branch --cov-fail-under=90`) and frontend
  (Vitest `coverage.thresholds` = 90). CI/pre-commit fails below this.
- **No live cluster in unit tests.** LXD/Ceph/OVN are mocked at the HTTP
  layer (`respx`) using recorded fixtures under `tests/fixtures/`. Optional
  integration tests against a real MicroCloud are opt-in via `MCUI_IT_CONFIG`
  (path to a config.toml, D32) and skipped otherwise.
- **Secrets never committed:** client certs/keys, fixtures scrubbed of real
  IPs/fingerprints. `.gitignore` covers `*.key`, `*.crt`, `*.pem`, `.env`, `data/`.
- Python setup is documented in README (venv + pip). Keep it accurate.

## Design decisions (ADR log)

| # | Decision | Rationale | Status |
|---|----------|-----------|--------|
| D1 | **Backend in Python** (>=3.10; dev VM is 3.10, MicroCloud hosts on 24.04 have 3.12) | Owner's preference. | Accepted 2026-11-05 |
| D2 | **FastAPI + Uvicorn**, fully async | Native async + WebSocket support (needed for terminal/console/event proxying), OpenAPI for free, easy testing via `httpx.AsyncClient`. | Proposed |
| D3 | **Own thin async LXD client on `httpx`**, not `pylxd` | pylxd is synchronous (requests), lags newer API extensions (OVN load balancers, replicators, placement groups…). A thin client over the REST API covers everything, is trivially mockable with `respx`, and we only model what we use. | Proposed |
| D4 | **Backend-for-frontend (BFF):** the server holds the LXD client cert/key and talks mTLS to LXD; browsers never see or need the cert | Browsers can't present a client cert without OS keychain installs; also lets us enforce our own auth/RBAC and audit. | Proposed |
| D5 | **Two LXD identities:** a full TLS client cert for API management, and a separate `--type=metrics` cert for scraping | Least privilege: metrics certs can only read `/1.0/metrics`. Either may be the same file if the owner prefers; config allows both. | Proposed |
| D6 | **Server TLS verification is pinned** to the cluster's shared `cluster.crt` (standalone: `server.crt`; or a CA if PKI is used) with configurable `server_name`; never `verify=False` | In a cluster every member presents the same cluster cert. It's self-signed and IP SANs often don't match, so pin it and override SNI/hostname. | Accepted 2026-11-05 |
| D22 | **Metrics scraped on API port 8443 by default**; dedicated `core.metrics_address` port supported via `[metrics] port` + per-member overrides; documented on both sides | Owner's choice for now; the dedicated port is the hardening path later. | Accepted 2026-11-05 |
| D23 | **Ceph mgr prometheus: module plus both extra settings kept on** on the test cloud (`exclude_perf_counters=false`, needed because MicroCeph deploys no ceph-exporter, and `rbd_stats_pools=lxd_remote`). The UI still treats them as **optional**: it detects their series and explains how to enable them if absent, since other clusters may not have them. Scrape every mgr host and use whichever answers with data | Checkpoint 2026-11-05: series confirmed (6 OSDs, 34 RBD images), active-mgr response 267 KiB in ~35 ms; owner chose to keep them on. Phase 4 builds the OSD latency and per-RBD-image views (MO-7a) against the recorded fixture. See docs/CEPH-METRICS.md. | Accepted 2026-11-05 |
| D24 | **Licence: MIT** (`LICENSE`). Dependencies must be MIT-compatible; copyleft libraries only as unmodified, separately loaded modules with their licence notices kept (e.g. spice-html5 for the VGA console, whose LGPL licence is to be confirmed when it's added). Never copy lxd-ui (GPL-3.0) code | Owner's choice. | Accepted 2026-11-05 |
| D25 | **VGA console and file browser are MVP** (IN-9, IN-11). The backend proxies the LXD console websockets (data + control) so the browser never talks to LXD directly | Owner promoted them; the proxy keeps the cert server-side (D4). | Accepted 2026-11-05 |
| D26 | **Live-test workflow:** the Claude session's dev VM **cannot** reach the test cloud (direct connects get "network unreachable"; the egress proxy refuses CONNECT with 403). So development and CI use recorded, scrubbed fixtures. The repo ships a recorder (`mcui record-fixtures`) and opt-in integration tests that the owner runs on the Mac and commits the scrubbed output | Keeps ≥90% coverage without live access; the owner does live validation. | Accepted 2026-11-05 |
| D27 | **Tooling specifics:** setuptools (>=77) with an editable install, argparse CLI (no extra dependency), pytest-asyncio auto mode, coverage branch + `fail_under=90` in pyproject; Vitest 4 (4.1.12, upgraded from 3 on 2026-11-05 for GHSA-82fw-gwwq-j7x9 / GHSA-qxg6-89mg-rqv8) + v8 coverage thresholds 90 on all four metrics; React 19 + Vite 6 + TS 5.8; the Vite dev server proxies `/api` (incl. websockets) to `MCUI_BACKEND` (default :8000). CI matrix: Python 3.10 and 3.12, Node 22 | Minimal, standard, fast. | Accepted 2026-11-05 |
| D28 | **Key-file permission rule:** refuse private keys with *any* group/other permission bits (OpenSSH-style), stricter than CF-1's "world-readable" wording | A group-readable key is just as leaked on a shared host. | Proposed |
| D29 | **TLS pinning implementation:** `ssl.create_default_context(cafile=server_cert)` (no system CAs) + client cert; hostname checked against `server_name` via httpx's `sni_hostname` request extension. No extra verify flags: OpenSSL accepts LXD's self-signed `CA:FALSE` cert as an anchor. Covered by real-handshake tests (`tests/test_tls.py`) | Verified on Python 3.14/OpenSSL 3.6; CI runs it on 3.10 and 3.12. | Accepted 2026-11-05 |
| D30 | **Sessions:** random 256-bit token in an HttpOnly, SameSite=Strict cookie (`Secure` unless `server.secure_cookies=false`); only its SHA-256 is stored. CSRF: per-session token returned by login/`/api/auth/me`, required as `X-CSRF-Token` on every non-GET request. Password change logs the user out everywhere. The last admin can't be deleted or demoted. Users, sessions and audit share one SQLite DB in `server.data_dir` | Simple, stateless for the browser, no extra deps beyond argon2-cffi (MIT). | Proposed |
| D31 | **Live metrics are scraped on demand** (at most every 10 s, the last two scrapes kept in memory) until the Phase 2 background scraper exists. CPU % = rate of `lxd_cpu_seconds_total` excluding `idle`/`iowait`/`steal`, divided by `lxd_cpu_effective_total` (VMs report 0 there on LXD 5.21, so their vCPUs are counted from the distinct `cpu` labels); network excludes `lo`; root FS is the `/` mount; a counter decrease is a reset | Gives Phase 1 live numbers without a scheduler; same rate maths will move into the store (D9). | Proposed |
| D32 | **Integration tests use one env var, `MCUI_IT_CONFIG`** (path to a normal config.toml), instead of the `MCUI_IT_URL/CERT/KEY` trio | The config already carries endpoints, both certs, server cert and `server_name`; one file avoids duplicating them. | Proposed |
| D33 | **`mcui serve` uses a uvicorn app factory** (`mcui.main:create_app`, settings from `$MCUI_CONFIG`; `--config` sets it) | Works with `--reload` (child processes re-read the env). | Accepted 2026-11-05 |
| D34 | **History stores only what the graphs use** (`backend/mcui/metrics/select.py`): per-instance memory/procs/disk/net/filesystem series, derived `mcui_instance_cpu_busy_seconds_total`, `mcui_instance_cpus`, `mcui_instance_memory_used_bytes` (replacing per-CPU/per-mode CPU counters), LXD daemon health, member load/memory and per-member pool usage, host NIC counters, and a Ceph allowlist (health, mon/mgr status, cluster capacity, OSD up/in/usage/latency/ops, pool I/O and capacity, PG states, all `ceph_rbd_*`). Skipped: Go runtime, `state` label, tmpfs/overlay/run mounts, lo/veth/docker NICs, disk partitions and loop devices (VMs report whole disk *and* partitions, so summing double-counts), Ceph BlueStore/BlueFS/RocksDB/MDS/Paxos internals. OVN networks report zero counters in `/networks/{}/state`, so per-OVN-network traffic will come from instance NICs | Live test: ~1,200 series on the test cloud (699 LXD + 499 Ceph samples per round) instead of ~4,300. | Accepted 2026-11-05 |
| D35 | **Ceph metrics are recorded from Phase 2**, so the Phase 4 Ceph views have history from day one. mgr endpoints default to every member on :9283; the last active mgr is tried first, failover is automatic | Owner agreed 2026-11-05. | Accepted 2026-11-05 |
| D36 | **Store format:** separate `metrics.db` (WAL). New samples go to a `head` table; each finished hour is packed per series into a zlib chunk (delta timestamps + XOR-delta float64) and a 1-minute rollup chunk; each finished day gets a 1-hour chunk. Rollups keep avg/max/increase (resets handled), so rate = increase/step at any resolution. Queries stitch packed chunks with on-the-fly rollups of unpacked data; resolution is chosen from retention. A 4× scrape-interval gap breaks rate continuity. All DB work runs on one dedicated thread. Estimated ~680 MB/year for the test cloud (1,200 series, pessimistic noise); NF-4's 1,000-instance target would need ~20× that, which is MO-14's job | Simple, dependency-free, fits our scale; the `MetricStore` facade keeps a later switch cheap. | Proposed |
| D37 | **Collector loops:** metrics (LXD every member + Ceph) every `interval_seconds`; member state + host NICs every `state_interval_seconds` (60); compaction every 5 min. Errors are recorded per job (`/api/metrics/status`) without stopping the loops. With the collector running, the live view reuses its scrapes instead of scraping on demand | One scrape feeds both live and history. | Accepted 2026-11-05 |
| D38 | **Frontend libraries:** React Router 7.18.4 and uPlot 1.6.32 (both MIT), pinned exactly. Pages use real URLs (`/instances/:project/:name`, `/members/:name`, `/storage`, `/networks`), served by the SPA fallback; the time range lives in `?range=` so views are linkable. Charts are a thin uPlot wrapper; panels describe their queries declaratively and derive ratios (CPU %) and differences (filesystem used) client-side | Small, fast, no chart framework lock-in. | Accepted 2026-11-05 |
| D39 | **Per-network traffic = sum of instance NIC counters**, mapping guest interfaces to LXD networks by MAC (`volatile.<dev>.hwaddr` = guest `hwaddr`), because VM guests rename interfaces and OVN networks report zero counters. Instance index cached 60 s (`/1.0/instances?recursion=2&all-projects=true`) | Verified on the test cloud: VMs (`enp5s0`) and containers (`eth0`) map; docker/ZeroTier interfaces inside guests correctly don't. | Accepted 2026-11-05 |
| D40 | **Instance disk totals leave out `dm-*` devices** (plus partitions and loop devices, D34), via derived `mcui_instance_disk_{read,written}_bytes_total` used by Top-N and the live table. Per-device series still include `dm-*` so the instance page shows them. **Caveat:** an LXD pool with the `lvm` driver puts instance disks on `dm-*`; if one is added, totals must count those (check pool drivers) | Owner's `lsblk` on gw-ips-microcloud-1: `sdb3 → dm-0 (LUKS) → dm-1 (LVM root /)` and `→ dm-2 (LVM) → dm-4 (LUKS) → Ceph OSD`. Containers' I/O to the host root appears on both dm-0 and dm-1 (identical counters), so summing double-counted; their own disks are `rbdN`. | Accepted 2026-11-05 |
| D41 | **Live mutation tests are confined to an LXD project `mcui-test`** (tight limits, small Alpine containers) created on the test cloud for this purpose. Nothing outside it is changed by tests. Cluster-wide actions that can't be confined (member evacuate/restore, warning acknowledge/delete, cluster config) are fixture-tested only and live-tested by the owner | Owner approved 2026-11-05. | Accepted 2026-11-05 |
| D42 | **Changes return the LXD operation at once; the browser follows it.** Mutating endpoints return `{operation, metadata}` without waiting; the UI's action tracker polls `GET /api/operations/{id}?wait=30` until it ends and shows a toast. The audit entry is written when the change is started (with the operation id), or with the error if LXD refused it | Long operations (evacuation, image downloads) don't hold HTTP requests open; one code path for every action. | Accepted 2026-11-05 |
| D43 | **Browser websockets** (`/api/events`, later consoles): the socket is accepted, then closed with 4401 (not logged in) or 4403 (wrong Origin or role) so the browser can tell why; Origin's host must equal the request Host (no cross-site websocket hijacking); the Vite dev proxy keeps the browser's Host (`changeOrigin: false`). The events relay drops exact repeats because a clustered LXD sends every event twice (seen live) | Cookie auth alone isn't enough for websockets; SameSite=Strict plus the Origin check cover it. | Accepted 2026-11-05 |
| D44 | **Project selection is a per-browser UI setting** (localStorage), "All projects" by default; lists use `all-projects=true` and filter client-side | No server state; matches how lists already work. | Accepted 2026-11-05 |
| D45 | **Instance config is saved as a whole** (LXD `PUT` with every writable field: description, config incl. volatile keys, devices, profiles, ephemeral, stateful, architecture). The form fields and the YAML editors edit one shared draft, so they never disagree; snapshot schedules use `PATCH` of just those keys. YAML values are coerced to strings (LXD config is string-only) and devices must have a `type` | `PUT` is the only way to remove keys or devices; sending what LXD returned plus edits avoids resetting fields. | Accepted 2026-11-05 |
| D46 | **"Delete" of a running instance stops it first** (force stop, then delete), like `lxc delete --force`, after a confirmation that says so. Path parameters (project, instance, snapshot, log file) are validated against LXD's name rules so encoded `..` or `/` never reach LXD URLs. The instance list cache is 10 s and is cleared after every change made through the UI | Matches CLI expectations; defence in depth for URL building; lists stay current. | Accepted 2026-11-05 |
| D21 | **Management cert is an unrestricted client cert or a fine-grained TLS identity with `server admin`**; restricted (project-confined) certs are unsupported for management. Metrics use a separate `--type=metrics` cert. Keys generated with `openssl ecparam` (LibreSSL-safe) | The app manages cluster-wide objects (pools, networks, members). See `docs/LXD-CREDENTIALS.md`. | Accepted 2026-11-05 |
| D7 | **Scrape every cluster member individually** for `/1.0/metrics` (members discovered from `GET /1.0/cluster/members`) | LXD only returns metrics for instances on the member queried. Respect LXD's 8s metrics cache: default interval 15s, minimum 10s. | Proposed |
| D8 | **Metrics storage: SQLite only for MVP** (WAL, rollups raw 15s → 24h, 1m → 14d, 1h → 1y) behind a pluggable `MetricStore` interface. Prometheus, VictoriaMetrics and other stores are **evaluated post-MVP** (MO-14) | Zero-ops for a small edge cluster; the interface keeps the later switch cheap. | Accepted 2026-11-05 |
| D9 | **Counters stored raw, rates computed at query time** (handle counter resets) | Same semantics as PromQL `rate()`; keeps ingest simple. | Proposed |
| D10 | **Frontend: React + TypeScript + Vite**, served as static files by FastAPI | Parity features (terminal, VGA console, complex forms) need a real SPA; xterm.js and spice-html5 are JS. Charts: uPlot (tiny, fast for time series). | Proposed |
| D11 | **Ceph metrics via ceph-mgr `prometheus` module** (port 9283, unauthenticated, active mgr only) plus LXD `storage-pools/<pool>/resources` | Standard MicroCeph path; scrape all mgr hosts and keep whichever answers. | Proposed |
| D12 | **OVN visibility via LXD API first**, optional node agent later | MicroOVN ships no Prometheus exporter. LXD exposes OVN networks, forwards, LBs, peers, ACLs, ACL logs, allocations. Deeper NB/SB/OVS stats need `microovn.ovn-nbctl` / `ovs-appctl` on nodes → Phase 5 optional agent or ovn-exporter. | Proposed |
| D13 | **UI auth: local users only for MVP** (argon2 hashed, roles admin/viewer); HttpOnly session cookie + CSRF token. Other login options (OIDC etc.) evaluated post-MVP | Owner: local is fine for now. | Accepted 2026-11-05 |
| D14 | **Config via `config.toml` + env overrides** (pydantic-settings) | 12-factor friendly, easy to template. | Proposed |
| D15 | **Tooling:** ruff (lint+format), mypy (strict on `app/`), pytest + pytest-asyncio + respx + pytest-cov; frontend ESLint, Vitest, Testing Library, Playwright (e2e, not counted in coverage) | Standard, fast. | Proposed |
| D16 | **Deployment: Python package + systemd unit, and an OCI container image** | Owner confirmed both. | Accepted 2026-11-05 |
| D17 | **Manual dev run documented for macOS** (venv, `uvicorn --reload`, Vite dev server proxying `/api` + websockets) purely for development, separate from production deploy docs | Owner prototypes on a Mac. | Accepted 2026-11-05 |
| D18 | **Stock `/ui` is treated as a behavioural reference only**: it is a static SPA calling the public LXD REST API, so full parity is achievable through our BFF. No lxd-ui (GPL-3.0) code is copied | Licence hygiene; we re-implement against the API. | Accepted 2026-11-05 |
| D19 | **Feature detection from `api_extensions`** (`GET /1.0`); unsupported features hidden, not errored. **Baseline LXD 5.21 LTS** | Test cloud runs LXD 5.21.8; the API reference in docs/ is from latest (6.x). | Accepted 2026-11-05 |
| D20 | **Requirements live in `docs/REQUIREMENTS.md`** with IDs + tiers (MVP/P2/Later); commits reference IDs (e.g. `IN-2`) | Traceability. | Accepted 2026-11-05 |

Statuses: *Proposed* → *Accepted* once the owner confirms; *Superseded by Dn* when changed.

## Status

- **Phase 0 (scaffold): done 2026-11-05.** FastAPI app factory, `/api/health`, SPA
  static serving with traversal protection, `mcui` CLI (`serve`), React+TS+Vite
  frontend showing backend health, Makefile, pre-commit hook, GitHub Actions CI.
  Coverage: backend 100%, frontend 100%.
- **Phase 1 (connectivity & live metrics): built 2026-11-05.** TOML+env config with
  key-permission checks (CF-1); async LXD client with pinned mTLS, `server_name`
  override and endpoint failover (CF-2, NF-3); `api_extensions` feature flags (CF-3);
  `mcui check`; Prometheus text parser; per-member metrics scraping with port
  overrides/fallback (CF-4) and live per-instance rates (MO-1); local users
  (argon2), sessions, CSRF, admin/viewer roles, audit log (AU-1/2/3); `mcui user …`;
  `mcui record-fixtures` with scrubbing (D26); React login + members + live
  instances dashboard. Coverage: backend 100%, frontend 100% lines / 98.6% branches.
  **Exit criterion met 2026-11-05** from the owner's Mac: `mcui check` OK on all 3
  members (metrics cert, port 8443), integration tests pass, live rates sane.
  Fixtures recorded to `backend/tests/fixtures/recorded/` (scrubbed; contain the
  owner's free-text instance descriptions, so committing is the owner's call).
  MO-7a checkpoint done 2026-11-05: Ceph extra stats confirmed and kept on (D23).
- **Phase 2a (collector, store, query API): built 2026-11-05.** Background
  collector (D37), SQLite chunked store with rollups and retention (D36), series
  selection (D34), Ceph recording (D35), `/api/metrics/{names,series,query,top,status}`.
  Validated live: 1,200 samples/round, LXD scrape 0.2 s, Ceph 0.07 s, member state
  + host NICs 5 s (sequential calls). The `dm-0`/`dm-1` question was resolved
  as stacked LUKS + LVM on the hosts (D40).
- **Phase 2b (UI): built 2026-11-05.** Router + layout; Overview (members,
  top-N with range, live table, links); Instances list; Instance page (CPU %,
  memory, disk throughput/IOPS, network bytes/packets/errors, filesystem used,
  processes, OOM kills); Member page (hardware from `/1.0/resources`, load,
  memory, instances' CPU/memory, host NICs, LXD API and daemon); Storage (LXD pool
  usage per member, Ceph pool usage/throughput/IOPS, raw capacity); Networks
  (per-network traffic from instance NICs, host interfaces). Recorder now also
  captures `/1.0/resources` with serials/UUIDs blanked. Coverage: backend 99.8%,
  frontend 100% lines / 99.5% branches / 94.8% functions.
  Vitest upgraded 3 → 4.1.12 (2026-11-05), clearing the critical dev-tool
  advisories. Remaining: moderate `minimatch` ReDoS (GHSA-g3hv-75wg-m249) via
  typescript-eslint, lint-time only; fixed only in minimatch 11.0.1 (2026-10-27),
  which typescript-eslint doesn't accept yet. Revisit after 2026-11-10. Runtime
  deps: no advisories.
- **Phase 3a (foundations): built 2026-11-05.** Operation helper, audited
  changes (D42), `/api/events` websocket (D43), Operations and Warnings pages,
  member evacuate/restore (fixture-tested only, D41), project switcher (D44),
  confirm dialogs, action toasts, YAML editor. Live: sandbox project created, its
  events seen once each through `/api/events`; cross-origin websocket refused.
- **Phase 3b (instances): built 2026-11-05.** List filters (status/type/member/
  profile/project) and bulk start/stop/restart/delete; instance page tabs:
  overview graphs, configuration (form + YAML, D45), snapshots (take, restore,
  rename, delete, new instance from, publish, schedule), logs; actions: lifecycle,
  force stop, rename, copy, move (member/pool/live), publish, delete (D46).
  Live in `mcui-test`: created an Alpine container and ran start/freeze/unfreeze/
  restart, config update, snapshot create/rename/restore/delete, logs, copy from
  snapshot, rename, move to another member, publish, delete and force delete, all
  audited with operation ids. The sandbox keeps the Alpine image and a published
  `mcui-test-image` for 3f.
- Next: **Phase 3c**, create instance (IN-2).

## Working conventions

- `make lint test` must pass before every commit (`make hooks` installs it as
  the pre-commit hook, which also checks the commit email).
- The Claude session's Linux dev VM keeps its venv outside the repo
  (`make … VENV=$HOME/mcui-venv`) and deletes `frontend/node_modules` after
  use, so no Linux binaries are left in the owner's macOS checkout.
- New Python deps go in `backend/pyproject.toml`; new JS deps via `npm install`
  (commit `package-lock.json`). Check licences (D24). Pin exact versions at
  least two weeks old.
- npm 10.9 (bundled with Node 22) can crash with `Cannot read properties of null
  (reading 'edgesOut')` when changing deps; use `npx -y npm@11.6.2 install …`
  to update the lockfile. `npm ci` with npm 10 reads the result fine.

## Test cloud facts (no secrets or IPs here)

- LXD 5.21.8 on gw-ips-microcloud-1, 5.21.9 on -2 and -3 (seen 2026-11-05), clustered (MicroCloud). Cluster cert SAN: `gw-ips-microcloud-1`
  plus loopback, so set `server_name = "gw-ips-microcloud-1"` and connect by IP.
- Management cert: unrestricted client cert (option A). Metrics cert:
  `--type=metrics`. Both verified against `/1.0` and `/1.0/metrics` on :8443.
- MicroCeph (Ceph 19.2.3 squid) mgr `prometheus` module **enabled**; `exclude_perf_counters=false` and `rbd_stats_pools=lxd_remote` turned on 2026-11-05 and **kept on** (D23). Active mgr was gw-ips-microcloud-1; standbys answer 200 with an empty body.
- Metrics scraped on 8443 (no `core.metrics_address`) for now; see D22.
- Members are on the 172.20.200.0/24 lab network (.11/.12/.13 = gw-ips-microcloud-1/2/3).
- Host disks: OS on LUKS (`dm-0`) + LVM (`ubuntu-vg`: root LV `dm-1`, an OSD LV); OSDs are LUKS-encrypted (`luksosd-N`). LXD pools: `remote` (Ceph RBD), `remote-fs` (CephFS) only. **No local instance storage** (no zfs/dir/lvm/btrfs pools), so only Ceph-backed instances are exercised; see Known gaps. Not reachable from the Claude dev VM (D26); reachable from the owner's Mac.
- Dev certs live outside the repo in `~/.config/mcui/certs/` on the owner's Mac; dev config is `~/.config/mcui/config.toml` (data in `~/.local/share/mcui`).
- **Sandbox project `mcui-test`** (D41), created 2026-11-05: own images, profiles and volumes (`features.images/profiles/storage.volumes=true`), shared networks; default profile has a 2 GiB root on `remote` and **no NIC**, `limits.cpu=1`, `limits.memory=256MiB`; project limits 3 containers, 0 VMs, 2 CPU, 1 GiB RAM, 6 GiB disk. Remove with: delete its instances and images, then `lxc project delete mcui-test`.
- LXD's filesystem metrics may omit a VM's `/` mount (seen on podman-1), so root FS can be unknown.

## Known gaps (not testable on the test cloud yet)

The test cloud only has Ceph-backed storage, so everything below is either
unbuilt or built but untested against real data. Close each one with recorded
fixtures from a cluster (or a throwaway LXD VM) that has the pool type.

| Gap | Risk | Status |
|---|---|---|
| **Local storage pools** (zfs, dir, lvm, btrfs) | Pool capacity history (MO-6) reads `/cluster/members/{}/state` `storage_pools`, which should cover them but is unverified. Instance disk totals (D40) skip `dm-*`: an **lvm** pool, or a **dir** pool on a LUKS/LVM host root, would make instances' disks invisible in totals. zfs VM volumes appear as `zdN` and containers on zfs as the pool's physical disks, both counted but unverified. Management of local pools (ST-2/ST-3) is Phase 4 | Unbuilt management; metrics untested |
| **Standalone (non-clustered) LXD** | Member state is skipped on 404 (no host load/pool usage); everything else uses the single endpoint | Fixture-tested only |
| **Other network types** (bridge-managed, macvlan, sriov) | Instance-NIC mapping by MAC (D39) should work; host-NIC collection filters by type | Untested live |

## Repo layout (target)

```
backend/            Python package `mcui`
  mcui/lxd/         async LXD REST client, operations, websockets
  mcui/metrics/     scraper, parser, store (sqlite / prometheus), query API
  mcui/api/         FastAPI routers (BFF)
  mcui/auth/        users, sessions, audit (SQLite)
  mcui/config.py    settings (TOML + env)
  mcui/check.py     `mcui check`; mcui/record.py `mcui record-fixtures`
  tests/            pytest; fixtures/lxd hand-written, fixtures/recorded from the
                    test cloud; integration/ opt-in live tests (MCUI_IT_CONFIG)
frontend/           React + TS + Vite SPA
docs/PLAN.md        phased delivery plan
Makefile            dev tasks (make help)
scripts/pre-commit  lint+test+email gate
CLAUDE.md           points Claude Code at this file
docs/REQUIREMENTS.md  requirement IDs and tiers
docs/lxd-api-surface.md  LXD REST path reference
docs/CONFIGURATION.md    config reference (tool side)
docs/LXD-CREDENTIALS.md  certs + LXD-side setup
docs/CEPH-METRICS.md     MicroCeph mgr prometheus setup
```
