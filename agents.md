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
| D1 | **Backend in Python** (>=3.10; dev VM is 3.10, MicroCloud hosts on 24.04 have 3.12) | Owner's preference. | Accepted |
| D2 | **FastAPI + Uvicorn**, fully async | Native async + WebSocket support (needed for terminal/console/event proxying), OpenAPI for free, easy testing via `httpx.AsyncClient`. | Proposed |
| D3 | **Own thin async LXD client on `httpx`**, not `pylxd` | pylxd is synchronous (requests), lags newer API extensions (OVN load balancers, replicators, placement groups…). A thin client over the REST API covers everything, is trivially mockable with `respx`, and we only model what we use. | Proposed |
| D4 | **Backend-for-frontend (BFF):** the server holds the LXD client cert/key and talks mTLS to LXD; browsers never see or need the cert | Browsers can't present a client cert without OS keychain installs; also lets us enforce our own auth/RBAC and audit. | Proposed |
| D5 | **Two LXD identities:** a full TLS client cert for API management, and a separate `--type=metrics` cert for scraping | Least privilege: metrics certs can only read `/1.0/metrics`. Either may be the same file if the owner prefers; config allows both. | Proposed |
| D6 | **Server TLS verification is pinned** to the cluster's `server.crt` (or its CA) with configurable `server_name`; never `verify=False` | LXD certs are self-signed and IP SANs often don't match; pinning is safe and works. | Proposed |
| D7 | **Scrape every cluster member individually** for `/1.0/metrics` (members discovered from `GET /1.0/cluster/members`) | LXD only returns metrics for instances on the member queried. Respect LXD's 8s metrics cache: default interval 15s, minimum 10s. | Proposed |
| D8 | **Metrics storage: pluggable `MetricStore` interface**; default **SQLite** (WAL) with rollups (raw 15s → 24h, 1m → 14d, 1h → 1y); optional **external Prometheus/VictoriaMetrics** query backend | No embeddable Prometheus TSDB in Python; SQLite is zero-ops and adequate for a small edge cluster. Owners who already run Prometheus can point at it instead. | Proposed |
| D9 | **Counters stored raw, rates computed at query time** (handle counter resets) | Same semantics as PromQL `rate()`; keeps ingest simple. | Proposed |
| D10 | **Frontend: React + TypeScript + Vite**, served as static files by FastAPI | Parity features (terminal, VGA console, complex forms) need a real SPA; xterm.js and spice-html5 are JS. Charts: uPlot (tiny, fast for time series). | Proposed |
| D11 | **Ceph metrics via ceph-mgr `prometheus` module** (port 9283, unauthenticated, active mgr only) plus LXD `storage-pools/<pool>/resources` | Standard MicroCeph path; scrape all mgr hosts and keep whichever answers. | Proposed |
| D12 | **OVN visibility via LXD API first**, optional node agent later | MicroOVN ships no Prometheus exporter. LXD exposes OVN networks, forwards, LBs, peers, ACLs, ACL logs, allocations. Deeper NB/SB/OVS stats need `microovn.ovn-nbctl` / `ovs-appctl` on nodes → Phase 5 optional agent or ovn-exporter. | Proposed |
| D13 | **UI auth: local users first (argon2 hashed), OIDC later**; sessions are HttpOnly cookies + CSRF token | Simple start; Entra ID/OIDC in a later phase. | Proposed |
| D14 | **Config via `config.toml` + env overrides** (pydantic-settings) | 12-factor friendly, easy to template. | Proposed |
| D15 | **Tooling:** ruff (lint+format), mypy (strict on `app/`), pytest + pytest-asyncio + respx + pytest-cov; frontend ESLint, Vitest, Testing Library, Playwright (e2e, not counted in coverage) | Standard, fast. | Proposed |

Statuses: *Proposed* → *Accepted* once the owner confirms; *Superseded by Dn* when changed.

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
```
