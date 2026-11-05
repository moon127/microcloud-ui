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
  integration tests against a real MicroCloud are opt-in via env vars
  (`MCUI_IT_URL`, `MCUI_IT_CERT`, `MCUI_IT_KEY`) and skipped otherwise.
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
| D23 | **Ceph mgr prometheus: only the module is enabled for now.** The extra settings (`exclude_perf_counters=false`, needed because MicroCeph deploys no ceph-exporter, and `rbd_stats_pools` limited to LXD's pool) are **supported but optional**: the UI detects their series and explains how to enable them. Scrape every mgr host and use whichever answers with data | Owner deferred the load-adding settings on the small test cluster; code for both is built and fixture-tested in MVP (MO-7a); live test when the owner briefly enables them. See docs/CEPH-METRICS.md. | Accepted 2026-11-05 |
| D24 | **Licence: MIT** (`LICENSE`). Dependencies must be MIT-compatible; copyleft libraries only as unmodified, separately loaded modules with their licence notices kept (e.g. spice-html5 for the VGA console, whose LGPL licence is to be confirmed when it's added). Never copy lxd-ui (GPL-3.0) code | Owner's choice. | Accepted 2026-11-05 |
| D25 | **VGA console and file browser are MVP** (IN-9, IN-11). The backend proxies the LXD console websockets (data + control) so the browser never talks to LXD directly | Owner promoted them; the proxy keeps the cert server-side (D4). | Accepted 2026-11-05 |
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

## Test cloud facts (no secrets or IPs here)

- LXD 5.21.8, clustered (MicroCloud). Cluster cert SAN: `gw-ips-microcloud-1`
  plus loopback, so set `server_name = "gw-ips-microcloud-1"` and connect by IP.
- Management cert: unrestricted client cert (option A). Metrics cert:
  `--type=metrics`. Both verified against `/1.0` and `/1.0/metrics` on :8443.
- MicroCeph mgr `prometheus` module **enabled** (defaults only; perf counters and RBD stats deferred, see D23/MO-7a).
- Metrics scraped on 8443 (no `core.metrics_address`) for now; see D22.
- Dev certs live outside the repo in `~/.config/mcui/certs/` on the owner's Mac.

## Repo layout (target)

```
backend/            Python package `mcui`
  mcui/lxd/         async LXD REST client, operations, websockets
  mcui/metrics/     scraper, parser, store (sqlite / prometheus), query API
  mcui/api/         FastAPI routers (BFF)
  mcui/auth/        users, sessions, OIDC
  tests/            pytest; fixtures/ recorded LXD responses
frontend/           React + TS + Vite SPA
docs/PLAN.md        phased delivery plan
docs/REQUIREMENTS.md  requirement IDs and tiers
docs/lxd-api-surface.md  LXD REST path reference
docs/CONFIGURATION.md    config reference (tool side)
docs/LXD-CREDENTIALS.md  certs + LXD-side setup
docs/CEPH-METRICS.md     MicroCeph mgr prometheus setup
```
