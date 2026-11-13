# agents.md — microcloud-ui

Guidance for humans and AI agents working in this repo, plus the running log of
design decisions. **Update this file in the same commit as any change that
makes or alters a design decision.**

## Ground rules

- **Commit identity:** every commit is authored *and* committed as
  `gareth@woolridge.org.uk` (set via repo-local `git config user.email`; never
  rely on global config).
- **No session URLs in commit messages (D66):** end with the `Co-Authored-By` line
  only, never a `Claude-Session:` trailer or claude.ai session link.
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
| D24 | **Licence: MIT** (`LICENSE`). Dependencies must be MIT-compatible; copyleft libraries only as unmodified, separately loaded modules with their licence notices kept (e.g. spice-html5 for the VGA console, whose LGPL licence is to be confirmed when it's added). Never copy lxd-ui (GPL-3.0) code. spice-html5 0.2.1 (LGPL-3.0-or-later) is used unmodified as a separately loaded chunk; notices in `frontend/THIRD_PARTY_NOTICES.md` | Owner's choice. | Accepted 2026-11-05 |
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
| D41 | **Live mutation tests are confined to an LXD project `mcui-test`** (tight limits, small Alpine containers) created on the test cloud for this purpose. Nothing outside it is changed by tests. Cluster-wide actions that can't be confined (member evacuate/restore, warning acknowledge/delete, cluster config) are fixture-tested only and live-tested by the owner. **Phase 4 extension (owner approved 2026-11-11):** network/pool management is live-tested in full inside a throwaway LXD running in a VM in `mcui-test` (the project's VM limit raised for it, removed afterwards); on the real cluster, tests may also create and delete objects named `mcui-test-*` that are ACLs, forwards or a bridge network. **Phase 5 extension (owner, 2026-11-13):** OVN load balancers (description `mcui-test LB`) and pools named `mcui-test-*` on the owner's OVN networks (e.g. `legacy-infra`), with `mcui-test` containers attached to that network as backends; deleted after each run. No OVN uplink or Ceph pool changes on the real cluster (OVN/Ceph management goes to the owner's checklist) | Owner approved 2026-11-05. | Accepted 2026-11-05 |
| D42 | **Changes return the LXD operation at once; the browser follows it.** Mutating endpoints return `{operation, metadata}` without waiting; the UI's action tracker polls `GET /api/operations/{id}?wait=30` until it ends and shows a toast. A page can ask for progress (the create wizard does, after the owner found its toast-only feedback too easy to miss): it then polls with `wait=2` and shows LXD's `*_progress` metadata inline (download/unpack percentages) and locks its submit button. The audit entry is written when the change is started (with the operation id), or with the error if LXD refused it | Long operations (evacuation, image downloads) don't hold HTTP requests open; one code path for every action. | Accepted 2026-11-05 |
| D43 | **Browser websockets** (`/api/events`, later consoles): the socket is accepted, then closed with 4401 (not logged in) or 4403 (wrong Origin or role) so the browser can tell why; Origin's host must equal the request Host (no cross-site websocket hijacking); the Vite dev proxy keeps the browser's Host (`changeOrigin: false`). The events relay drops exact repeats because a clustered LXD sends every event twice (seen live) | Cookie auth alone isn't enough for websockets; SameSite=Strict plus the Origin check cover it. | Accepted 2026-11-05 |
| D44 | **Project selection is a per-browser UI setting** (localStorage), "All projects" by default; lists use `all-projects=true` and filter client-side | No server state; matches how lists already work. | Accepted 2026-11-05 |
| D45 | **Instance config is saved as a whole** (LXD `PUT` with every writable field: description, config incl. volatile keys, devices, profiles, ephemeral, stateful, architecture). The form fields and the YAML editors edit one shared draft, so they never disagree; snapshot schedules use `PATCH` of just those keys. YAML values are coerced to strings (LXD config is string-only) and devices must have a `type` | `PUT` is the only way to remove keys or devices; sending what LXD returned plus edits avoids resetting fields. | Accepted 2026-11-05 |
| D46 | **"Delete" of a running instance stops it first** (force stop, then delete), like `lxc delete --force`, after a confirmation that says so. Path parameters (project, instance, snapshot, log file) are validated against LXD's name rules so encoded `..` or `/` never reach LXD URLs. The instance list cache is 10 s and is cleared after every change made through the UI | Matches CLI expectations; defence in depth for URL building; lists stay current. | Accepted 2026-11-05 |
| D47 | **Remote image catalogues are read by the backend** from the simplestreams index of two named remotes only (`images` → images.lxd.canonical.com, `ubuntu` → cloud-images.ubuntu.com/releases), summarised to products that offer a container (`lxd.tar.xz` + squashfs/rootfs) or VM (`lxd.tar.xz` + disk image) build, filtered to the cluster's architectures, cached 6 h. The browser picks a remote by name; the backend sets the server URL, so the UI can't point LXD at arbitrary image servers | LXD has no "list remote images" API; `images:` is 0.3 MB and `ubuntu:` 18.8 MB, ~1 s each. **Amended 2026-11-12 (Phase 5b):** on LXD 6.10+ (`image_registries`) the picker uses the server's own registries instead: LXD lists their images (`/1.0/image-registries/{name}/images`, every serial and type, so the backend keeps the newest serial per product, cached 6 h as before) and instances name `source.image_registry`; any registry name is accepted there, custom ones included. The two named remotes stay for LXD 5.21 | Accepted 2026-11-05 |
| D48 | **Consoles via backend-held streams:** `POST …/exec` or `…/console` starts the LXD operation; the backend keeps the websocket secrets and the member URL (operations' websockets live on the member running them) and returns an opaque stream id. The browser attaches with `ws /api/streams/{id}` (admin, same user only, Origin-checked): binary frames are data, text frames are LXD control messages. The backend attaches LXD's **control** channel itself and holds it, ref-counted, while any browser connection is open, because LXD waits for every channel: seen live, a missing control channel left exec pending and a later force-stop failed with "unexpected EOF". SPICE opens several data connections to the same stream (subprotocol `binary`); they share one control channel. xterm.js and spice-html5 are lazy-loaded chunks | Secrets never reach the browser (D4); one browser socket per session. | Accepted 2026-11-05 |
| D49 | **Operation waits outlive LXD's own wait** (HTTP timeout = wait + 15 s), and an expired wait (LXD answers "context deadline exceeded") returns the still-running operation instead of an error | Seen live: 10 s client timeout vs 30 s wait made long creates (45 s image download) look like "LXD unreachable". | Accepted 2026-11-05 |
| D50 | **File browser over LXD's `/files` API:** listings are names only, so each entry's type/mode/owner/size/mtime comes from a HEAD (works on 5.21 although `file_head` isn't advertised), 16 in parallel, first 500 entries; downloads and uploads stream through the backend (no buffering); paths must be absolute and are normalised; deletes are not recursive (LXD refuses non-empty folders) and `/` can't be deleted; changes audited as `file.*` | Verified live: 3 MB upload/download round trip byte-identical with mode applied. | Accepted 2026-11-05 |
| D51 | **Profiles, images, projects:** profiles and projects are edited as a whole (description + config, plus devices for profiles) with the shared YAML/validation used for instances; the `default` profile and project can't be renamed or deleted (UI and API). Image upload takes a **unified tarball** as the raw body (streamed); split meta+rootfs uploads are not supported yet; an alias given with an upload is added once LXD reports the fingerprint (waits up to 10 min). Remote imports use the named remotes only (D47). Project create/rename/delete are fixture-tested only (outside the sandbox, D41) | Covers PR-1, IM-1/2, PJ-1 at MVP depth. | Accepted 2026-11-05 |
| D52 | **Networks (NW-1/2):** the list stays in `catalog` (with `target` for a member's own interfaces); detail, per-member state, per-member settings, leases and allocations are read straight from LXD. In a cluster, bridge/physical/macvlan networks are created in LXD's two steps (a pending definition per member carrying `parent` / `bridge.external_interfaces` / `bgp.*.nexthop`, then the cluster-wide one), audited as one change; if a step fails the half-made network is deleted so the name is free again. OVN networks are one step; on a standalone server the per-member keys go in the config. Edits replace the cluster-wide description+config, or with `target` one member's own keys; LXD applies a member-level PUT's description cluster-wide (seen live: it blanked it), so the backend resends the current description. Network names are limited to 15 characters (Linux interface names) | Mirrors `lxc network create --target`; one form for all four types. | Accepted 2026-11-11 |
| D53 | **ACLs and forwards (NW-4 CRUD, NW-5):** ACLs get their own pages (list; detail with an ingress/egress rule table, rename, delete, used-by); rules are validated by the backend (action, state, protocol enums) and LXD; empty rule fields LXD leaves out are filled in on load. Attaching is done where LXD keeps it: the network page has an ACL section (checkboxes + default ingress/egress action) that edits the network's `security.acls*` keys in the same draft as the YAML, saved with Save; NIC-level `security.acls` stays in the instance's device YAML. Forwards live on the network page (bridge and OVN), keyed by listen address (backend checks it's an IP); bridge forwards in a cluster belong to a member (LXD sets `location` even without `target`), so edits/deletes send it back as `target`. Seen live: bridge ACLs refuse `@internal`/`@external` (OVN-only) and LXD refuses to rename or delete an ACL in use; the UI says so | One place per thing LXD stores; no hidden cross-object writes. | Accepted 2026-11-11 |
| D54 | **Storage pools (ST-1/2):** the two-step cluster create of D52 moved to `mcui.lxd.clustered` and is shared: local drivers (dir, zfs, btrfs, lvm) get a pending definition per member with that member's `source`/`size`/`zfs.pool_name`/`lvm.vg_name`/`lvm.thinpool_name`, then the cluster-wide one; Ceph/CephFS are one step and refuse per-member keys. Pool page: per-member capacity table (from each member's `/resources`, with a meter), capacity history (MO-6 series), cluster-wide YAML, per-member settings (local drivers only; a member-level PUT keeps the description as in D52), used-by, delete. Seen live: CephFS reports no `used` space, so used/percent show “–” | Same model as networks; one helper for both. | Accepted 2026-11-11 |
| D55 | **Custom volumes (ST-3) and safer config saves:** volumes are listed per project across pools (snapshots, which LXD lists as `vol/snap`, are left out); a volume on a local pool in a cluster lives on one member, and every call about it sends that `location` back as `target`. Volume page: usage, size (resize), snapshot schedule/expiry/pattern as fields and the rest as YAML, snapshots (create with optional expiry, rename, restore, delete), used-by with detach, attach dialog. Attach = instance PATCH adding a disk device (LXD merges devices); detach = GET + PUT without it, guarded by the ETag, refused (409) if the device isn't this volume or comes from a profile. **Config PUTs keep LXD's `volatile.*` keys** (`mcui.lxd.volatile`) for volumes and cluster-wide pool/network saves: seen live, a size-only PUT dropped a volume's volatile keys and its snapshots then failed. **A failed operation's wait** answers with an error in LXD; `operations.wait` now returns the finished operation (status Failure + err) instead of a 502 (extends D49). Seen live: ZFS restores only the latest snapshot unless `zfs.remove_snapshots`; restoring or renaming a volume used by a running instance is refused | One place per thing; protects state LXD can't rebuild. | Accepted 2026-11-11 |
| D56 | **Ceph dashboard (MO-7, MO-7a):** the “now” view comes from the collector's last ceph-mgr scrape (kept in memory, no extra mgr load): health status and checks, mons in quorum, active mgr, OSDs (host, class, up/in, PGs, used/size, apply/commit latency), PG states summed over pools, raw capacity, pools (stored, max avail, percent used, objects, PGs). History is the stored series: raw capacity, pool usage/throughput/IOPS (moved here from Storage), OSD apply/commit latency, per-op OSD read/write latency (rate of `_sum`/`_count`). Busiest RBD images over the range (bytes/s, ops/s, mean latency in ms from nanosecond counters) are mapped back to LXD objects by name: `<container|virtual-machine|custom|image>_[<project>_]<name>[.block]`, the project recognised from LXD's project list, `zombie_` = deleted | Uses what the collector already gathers; RBD names are LXD's own convention. | Accepted 2026-11-11 |
| D57 | **OVN overview (MO-9) from the LXD API only:** `/api/ovn` lists OVN networks of `default` and of projects with `features.networks` (others borrow default's, which LXD reports again under their name), each with uplink, subnets/NAT, external router address (`volatile.network.ipv*.address`), active gateway chassis (from network state), forward/load-balancer/peer counts (`null` when LXD can't list them), ACLs and allocation counts by type; uplinks (managed physical) with gateway, routes, DNS, BGP peers and how many of `ipv4.ovn.ranges` router addresses are used. The page flags networks not Created, without a gateway chassis, and uplink ranges ≥ 80% used. No OVN northbound/southbound access (that's MO-11, Later) | Everything comes from LXD; no extra credentials or agents. | Accepted 2026-11-11 |
| D58 | **Host metrics (MO-4, MO-12), brought forward from Phase 5:** an Overview **Hosts** table built from what LXD gives (member state: load, coarse memory, swap; instances: running/total, their CPU and memory use from live metrics; **allocated** vCPUs and memory of running instances from `limits.cpu` (count or pinned set) and `limits.memory` (size or % of host), VMs without limits counted at LXD's 1 vCPU/1 GiB, containers without limits counted as “unlimited”; host NIC rate from history), overlaid with **node_exporter** when a host has it (`/api/hosts`). The collector scrapes `<scheme>://<member host>:9100/metrics` with the LXD scrape (optional TLS with a pinned CA, `server_name`, basic auth from a 0600 password file) and stores only: CPU seconds **summed over CPUs per mode**, memory (total/available/free/cached/buffers/swap, ZFS ARC), filesystems except pseudo/snap mounts, whole host disks (not partitions, dm-*, md, rbd/nbd/zd, loop), pressure (cpu/memory/io waiting, memory/io stalled), oom-kill and swap-in/out counters, clock sync. A host with nothing listening is “not installed”, not an error; auth/TLS failures are errors in the job status and `mcui check` (warnings there, never a failure). CPU % = 100 − idle − iowait − steal (as for instances). Member page shows node graphs when present and LXD's memory estimate otherwise. Install/lock-down: `docs/HOST-METRICS.md` (TLS + basic auth via `--web.config.file`, bind to the management IP; no ufw or stock nftables.conf on MicroCloud hosts) | LXD has no host CPU %, disk or true memory figures; node_exporter is the standard way to get them and stays optional. | Accepted 2026-11-11 |
| D59 | **Screenshots from an invented demo cluster:** `make screenshots` builds the SPA, serves it with `vite preview` and drives the installed Google Chrome (or `$CHROME_PATH`) through `playwright-core` 1.64.1 (Apache-2.0, dev only; no browser download). Every `/api/*` request is answered from `frontend/screenshots/demo.ts`: three hosts `node-1..3` on 192.0.2.0/24 and 2001:db8::/32, made-up instances/projects/pools/Ceph/OVN, and smooth deterministic metric history (fixed clock, UTC). The demo is typed against `src/api.ts`, and a test checks every metric the shown pages query has data, that unknown paths are refused and that only documentation/private addresses appear. Never screenshot a real cluster for the repo. **Amended 2026-11-13:** the demo runs MicroCloud 3.4 LTS (LXD 6.10.1 with the LXD 6 features, Ceph Tentacle, 26.04 images, a placement group, image registries, and an OVN load balancer with a health-checked pool), and two screenshots show 3.4-only pages (`placement-groups.png`, `load-balancers.png`). The README says 2.x looks the same without those parts | Shareable, reproducible images with nothing about the owner's network; regenerate whenever the UI changes. | Accepted 2026-11-11 |
| D83 | **Phase 7 is backups, project moves and ISO upload,** pulled forward from the parity list: instance and volume backups (create, rename, delete, download, import; IN-13, ST-4), moving instances between projects (IN-6), ISO upload and attach (IM-3), and editing the `restricted.*` keys (part of PJ-2). Rights: backups operate (a download holds the files), imports and ISOs manage in the target project, a project move manage in both, restriction keys cluster admin. The metric store becomes Phase 8 and the rest of the parity list Phase 10. Plan: `docs/PLAN.md` Phase 7 | Owner, 2026-11-13: these first. Checked live first: a restricted project refuses snapshots and backups unless `restricted.snapshots` / `restricted.backups` are `allow`, so Phase 6's operator snapshots need 7e. Volume backup export/import and ISO upload work on 6.10.1. | Accepted 2026-11-13 |
| D82 | **Audit page, and the security model spelled out:** the audit log gets a page (everyone; the server filters it as D81 says). `docs/USERS.md` opens with "Read this before deploying", with pointers from the README and `docs/DEPLOY.md`. It says plainly that the app reaches LXD with one admin certificate for every user, so it re-implements part of LXD's security model itself. The app is the boundary between users; LXD's `restricted` guards the host but not other projects; the host, config, data directory and certificate are as sensitive as LXD admin access; LXD's logs credit every change to the app, so who did it is only in the Audit page; and users live in the app, not LXD. Per-user LXD identities (OIDC, AU-4/AC-2) would remove most of this, later | Owner, 2026-11-13: deployers should be aware of it. | Accepted 2026-11-13 |
| D81 | **Users and project access (Phase 6), proposed:** the app enforces project grants (viewer / operator / admin per project, on top of the global cluster admin and viewer roles) with its one admin connection to LXD, rather than per-user LXD identities. It relies on a verified fact: LXD enforces a project's `restricted=true` against the admin certificate, at creation and at edit (privileged, host-path disks, unix-char, raw.lxc, nesting, privileged profiles all refused). Operator and admin grants only on restricted projects. One `authorize` check, a declared policy per route, a test over every route, server-side filtering of lists, metrics, operations, audit and events, and 404 for other projects. Later phases renumbered: metric store 7, parity 8, packaging 9. Plan: `docs/PLAN.md` Phase 6 | Owner, 2026-11-13: users tied to projects before the metric store or OIDC. App-enforced works the same on LXD 5.21 and 6.x and needs nothing on the cluster; the restricted-project test showed LXD still guards the routes to the host. Owner approved the plan and its five defaults; it leads to 0.2.0. | Accepted 2026-11-13 |
| D80 | **Repository protection:** the owner stays the only collaborator (forks and pull requests welcome). Two rulesets: `main` can't be force-pushed or deleted, with no bypass, so a planned history rewrite switches it off first; release tags `v*` can't be deleted or moved, with the admin role as bypass, and `edge` is excluded since it moves with every build. Workflows from every outside contributor need approval (it was first-time contributors only). Private vulnerability reporting is on, with `SECURITY.md`. Pull requests and passing checks aren't required on `main`: the workflow is commit and push, gated by the pre-commit hook and CI. Immutable releases stay off because edge is updated in place | Owner, 2026-11-13: a public project where only the owner pushes. Settings applied through the API; documented in `docs/RELEASING.md` | Accepted 2026-11-13 |
| D79 | **OSD % used against the cluster's own thresholds:** the Ceph page's OSD table shows % used per OSD. The meter turns amber at the nearfull ratio and red at the full ratio, and the figure is marked "nearfull" or "full" past them. The ratios come from the mgr's `ceph_osd_nearfull_ratio` and `ceph_osd_full_ratio` (Ceph Tentacle exports them). Squid doesn't, so Ceph's defaults (85%, 95%) apply, and a line above the table says which. Backfillfull isn't exported by either, so it isn't shown | Owner: see OSDs approaching NEARFULL before Ceph warns. The real ratios matter because admins change them. | Accepted 2026-11-13 |
| D78 | **Bulk actions under one toast:** the Instances page's Start/Stop/Restart/Delete on a selection still make one call per instance, run together, but report as one toast ("Stop 5 instances: 3 of 5 done", then ✓ or the names that failed and why) through `runAll` in the actions provider; lists refresh as each one lands. LXD 6's bulk call (`PUT /1.0/instances`, `instance_bulk_state_change`) isn't used: it takes only `{"state": …}` and acts on every instance in the project, with no way to name instances | Owner chose this over a project-wide action (2026-11-13). Works the same on LXD 5.21 and 6.x. | Accepted 2026-11-13 |
| D77 | **Ubuntu 22.04 dropped:** bundles for 26.04 (py3.14) and 24.04 (py3.12) only, four per release; CI tests 3.12 and 3.14; `requires-python >=3.12`, ruff and mypy target 3.12. The `tomli` backport and bundle.sh's per-Python extras go (a test now fails if a Python-version-limited dependency comes back, since `pip download` would miss it). Ruff's ASYNC109 is ignored: our `timeout` parameters are httpx request timeouts and LXD's operation-wait length, not asyncio deadlines. Supersedes D1's ">=3.10" and D72's six bundles. 22.04 hosts can still run the container image | Owner: 22.04's standard support ends April 2027 and the test cloud runs 26.04; nothing in the vulnerability scans was 22.04-specific. | Accepted 2026-11-13 |
| D76 | **Watching for vulnerabilities:** Dependabot alerts and security updates are on (repository settings); `.github/dependabot.yml` asks for weekly grouped updates of npm, pip and Actions, minor and patch only (majors are planned upgrades; security updates still bring them). The image drops `pip` from its venv after installing (the scan flagged python3-venv's old copy). `make audit` (`pip-audit` on the backend's runtime dependencies, `npm audit --omit=dev --audit-level=high` on the frontend) runs in CI and so before every release. The weekly `security.yml` repeats the audits, adds build tools, and scans the published `:latest` image with Trivy (fixable HIGH/CRITICAL only); findings go to the Security tab and one `rebuild-needed` issue, which the next clean scan closes. A rebuild is a manual patch release, not automatic | Owner: keep up with CVEs in the dependencies and flag rebuilds. Python deps are minimum-pinned and the image is rebuilt from `ubuntu:26.04`, so a release picks up fixes; automatic releases would fire on every Ubuntu update. | Accepted 2026-11-13 |
| D75 | **Evacuation safety and progress** (after C2a on the test cloud). The evacuate dialog warns, without blocking, in two cases. First, when another member is already evacuated or offline: it names where every instance would run and how many online database voters would be left, because LXD 6.10 doesn't always refuse a second evacuation. Second, when running stateful VMs would live-migrate (default or `auto` mode, `live-migrate`) and the members' CPU models differ: a VM moved to a newer CPU can't come back. The restore dialog names the VMs to stop first. While an evacuation or restore runs, the member page shows LXD's progress text (any `*progress` metadata key) and the instances on the member, polled every 3 s through `/api/instances?fresh=true`; the buttons are hidden meanwhile. The instance list gains `stateful`, `evacuate` and `evacuated_from` | Today's test put every instance on one member and failed two restores; LXD gave no warning either time. Warnings rather than blocks: the admin may know better. | Accepted 2026-11-13 |
| D74 | **"MicroCloud 3.4 in a box" for development:** until the test cloud is upgraded, Phase 5 is developed against a throwaway VM in `mcui-test` (Ubuntu 26.04, 4 vCPU, 8 GiB, 40 GiB on `remote`, NIC on `legacy-infra`; project limits raised to 1 VM/4 CPU/8 GiB/50 GiB and restored after) running the 3.4 component snaps (LXD 6.10.1 `6/stable` as a one-member cluster, MicroCeph Tentacle `tentacle/stable` with three loop OSDs, MicroOVN `26.03/stable`, MicroCloud `3/stable`), wired by hand as MicroCloud does (snap connections, Ceph RBD + CephFS pools, a bridge as OVN uplink). Certificates are made in the VM and trusted only there; the owner's keys never leave the Mac. The Mac reaches the VM's API on `legacy-infra`, so `mcui serve`/`check`/`record-fixtures` run against it. `api_extensions`-gated API differences the UI adapts to (not hides) live in `ADAPTS` (first: `cephfs_path` = `storage_remote_drop_source`) | The owner asked for development to start before the cluster upgrade; real LXD 6.10/Tentacle beats docs (it found the CephFS break, the Tentacle perf-counter gap with its exact fix, and the operation shapes). | Accepted 2026-11-12 |
| D73 | **Phase 5 is MicroCloud 3.4 LTS support; later phases renumbered:** the metric store (was 5) is now 6, remaining parity (was 6) is 7, packaging (was 7, done) is 8. Older rows keep the old numbers (D58 "Phase 5" = now 6; D63–D65 "Phase 7a–c" = now 8a–c). Plan in `docs/PLAN.md`. MicroCloud 2.x stays supported (5.21 fixtures recorded first); 5b scope: placement groups, image registries, OVN load balancers, child operations, multi-volume snapshots; disaster recovery later; identities/OIDC wait for a discussion on the auth model (the app talks to LXD as one admin client) | Owner: supporting the current MicroCloud LTS (LXD 6.10, Ceph Tentacle, MicroOVN 26.03) matters more than the metric store; the test cloud moves from 2.1 to 3.4 first. | Accepted 2026-11-12 |
| D72 | **Ubuntu 26.04 LTS:** releases and edge add py3.14 bundles (26.04 ships Python 3.14), six in all; CI tests 3.10, 3.12 and 3.14. The image moves to `ubuntu:26.04` (both build and runtime stages): one image, tags unchanged, 219 MB (was 185 MB on 24.04). Verified in an Ubuntu 26.04.1 VM in `mcui-test` (on `legacy-infra`): the py3.14 bundle via `install.sh` as a service, and the image built with Podman 5.7 and run as the Quadlet (read-only, no capabilities, uid 8843, healthcheck). Also tested 2026-11-12: a 24.04.5 VM with the 0.1.2 py3.12 bundle from the GitHub Release, `do-release-upgrade` to 26.04.1 (22 min; it first wants a reboot after pending updates), then the service crash-loops (`No module named 'mcui'`, its venv's packages are under python3.12) until the py3.14 bundle is installed; users, data and the self-signed cert are kept; pre-upgrade rollback directories no longer run (DEPLOY.md §7). Found on the way: DEPLOY.md said switching bundle ↔ container was "stop one, start the other", but both are `mcui.service` and the bundle's unit in `/etc/systemd/system` hides the Quadlet's; corrected (`install.sh --uninstall` first) | Owner: support 26.04. A container doesn't need to match the host's Ubuntu, so one image on the newest LTS (supported to 2031) beats variants; bundles must match the host's Python, so one per LTS. | Accepted 2026-11-12 |
| D71 | **`mcui` falls back to `~/.config/mcui/config.toml`:** the config is `-c`, else `MCUI_CONFIG`, else that file if it exists, else none (built-in defaults: no cluster, data in `./data`). `mcui serve` prints which on stderr first. Tests point the default at an empty temp dir (autouse fixture) so a developer's real config never leaks in | The owner followed the README's `make serve` without `MCUI_CONFIG` and got an unconfigured server with an empty user list; the README also wrongly claimed this fallback existed. Production is unaffected: the systemd unit passes `-c /etc/mcui/config.toml` and the image sets `MCUI_CONFIG`. | Accepted 2026-11-12 |
| D70 | **Hosts table fits a laptop screen:** it needed 1662px (overflowing below ~1730px wide). Now: secondary details on their own muted line (`.sub`: CPU iowait/steal, disk busy, "+ N unlimited"); a shared unit written once (`formatPair`: `18.0 / 64.0 GiB`, `3.0 / 1.8 MiB/s`; both units when they differ); "Net rx / tx" as in the Instances table; gauges in table cells 3rem wide (`td meter`, browser default ~80px); cell padding 0.5rem (was 0.6rem) in every table. Measured with headless Chrome on the demo cluster: 1216px, fits a 1280px window; host names much longer than `node-1` need ~1360px. `.scroll` still scrolls anything narrower | The owner wanted the whole table visible on a larger screen; details that only appear sometimes shouldn't set the width of every row. | Accepted 2026-11-12 |
| D69 | **Numeric table columns are right-aligned, header included:** `td.num` (right, tabular figures) needs a matching `th.num`. Before this, 36 headers stayed left while their values sat right, so on wide screens titles drifted far from their numbers (owner noticed in Safari; every browser does it). `src/test/numericColumns.ts` runs after every frontend test (in `setupTests.ts`) and fails it if a rendered table has a `td.num` column whose header lacks `num` | Numbers compare best right-aligned; the header must follow its column. A check on every rendered table catches new columns without a per-page test. | Accepted 2026-11-12 |
| D68 | **Edge channel (tip of `main`):** `release.yml` also runs on `workflow_run` of CI, for runs that passed on a **push** to this repository's `main` only (not PRs, not forks). It builds that commit (`head_sha`) the same way as a release, stamped `X.Y.Z.devN` (N = commits since the last `v*` tag, `scripts/build_version.py dev`; the source keeps `.dev0`), and publishes `ghcr.io/moon127/microcloud-ui:edge` + `:edge-<sha7>` and a rolling GitHub pre-release `edge` (tag force-moved via the API, `--latest=false`, old bundles replaced). CI isn't re-run for edge; a newer push cancels a running edge build (`concurrency`). One workflow with modes `release` / `edge` / `dry-run` decided in a `setup` job; downstream jobs use `!cancelled()` conditions because `ci` is skipped for edge. Release notes start from the previous `v*` tag (`--notes-start-tag`) so the edge pre-release never becomes their base. Old `edge-<sha>` images aren't pruned automatically (deleting by digest risks breaking multi-arch indexes) | Owner: anyone following development gets a bundle or image of the tip of `main`; built on every push (not nightly), named edge so `latest` stays release-only. devN is unique on a linear `main` and is a valid PEP 440 and filename-safe version. | Accepted 2026-11-12 |
| D67 | **Releases:** pushing a tag `vX.Y.Z` runs `release.yml`: CI (`ci.yml` via `workflow_call`), then `scripts/build_version.py release` (tag must be exactly `v` + `mcui.__version__`, final X.Y.Z only), then the image for linux/amd64 + linux/arm64 pushed to **GHCR only** (`ghcr.io/moon127/microcloud-ui`: `X.Y.Z`, `X.Y`, `latest`). Each platform builds on a **native runner** (`ubuntu-24.04`, `ubuntu-24.04-arm`, free as the repository is public) and is pushed by digest; a merge job joins them with `imagetools create`. Emulated arm64 (QEMU) was tried first in a throwaway VM: apt and Python byte-compiling alone took 15+ minutes. The SPA stage stays on `$BUILDPLATFORM` for local cross-builds, then a GitHub Release with the four offline bundles (py3.12/py3.10 × x86_64/aarch64). A hand-run (`workflow_dispatch`) is a dry run: builds everything, pushes nothing. Found on the way: `pip download --python-version` judges markers by the running Python, so 3.10 bundles lacked `tomli`; `bundle.sh` now names it (test keeps it in step with pyproject) | GHCR needs no extra account or secret (`GITHUB_TOKEN`); the owner chose it over Docker Hub. Tag-equals-version keeps image tags, bundle names and `mcui --version` in agreement. | Accepted 2026-11-12 |
| D66 | **No Claude session URLs in git history:** commit messages end with the `Co-Authored-By` line only. The early commits made from the desktop app carried a `Claude-Session:` trailer; history was rewritten (2026-11-12, messages only: trees, authors and dates unchanged) to remove it. A second rewrite the same day removed an accidentally committed `Claude outputs/` folder (unrelated documents) and the Finder `.DS_Store` files (which named it) from every commit; both are now in `.gitignore`. `scripts/commit-msg` (installed by `make hooks`, shellchecked) rejects any message with the trailer or a claude.ai session link | Session links are private to the owner's account and mean nothing to other readers; a hook stops them coming back. | Accepted 2026-11-12 |
| D65 | **Behind a reverse proxy (Phase 7c):** for short-lived certificates (Let's Encrypt) the proxy owns HTTPS and mcui serves plain HTTP to it (`tls_cert`/`tls_key` unset, `secure_cookies` stays true). Two settings: **`[server] public_url`** (normalised `scheme://host[:port]`; also accepted as the websocket Origin, for proxies that rewrite `Host` or drop a non-standard port; other origins still refused) and **`[server] forwarded_allow_ips`** (passed to uvicorn; IPs/CIDRs trusted for `X-Forwarded-For`, so logs show the client). Recipes in DEPLOY.md §10 for NGINX Proxy Manager, Cosmos Cloud, Caddy, nginx. **Verified live 2026-11-12** in a throwaway Ubuntu 24.04 VM (`mcui-test`, limits restored, VM and image deleted) with mcui as the bundle and each proxy at its defaults: sign-in with a Secure cookie, websocket Origin, a 150 s idle websocket (uvicorn's 20 s pings beat NPM's 90 s read timeout), a 50 MB upload, forwarded client IP. Findings: NPM 2.17.0 forwards `$host` plus the client's port (older releases used `$host`; `public_url` covers both) and needs `/etc/letsencrypt` mounted; Cosmos keeps `Host` by default, but *Spoof hostname* rewrites it (refused → fixed by `public_url`, tested), and its route **Timeout is a total deadline that cuts websockets** (default 4 h; a busy socket closed at a 60 s setting); Caddy keeps `Host`. NPM/Cosmos run on Docker: never on a member. Not tested: real Let's Encrypt (no public DNS in the VM). Proxy-side login trusted by mcui (single sign-on) left for AU-4 | The owner asked for proxy setups because copying short-lived certificates in is a chore. Uncovered two settings the code needed. | Accepted 2026-11-12 |
| D64 | **Container image (Phase 7b):** `deploy/container/Containerfile`, three stages (node:22-bookworm-slim builds the SPA; ubuntu:24.04 builds a venv with the wheel and SPA; ubuntu:24.04 runtime + python3, openssl, ca-certificates). Owner chose plain `ubuntu:24.04` and **no rockcraft/chisel for now**. Runs as uid/gid **8843** with no capabilities and a read-only root filesystem; the image ships `deploy/config.example.toml` as `/etc/mcui/config.toml` (`MCUI_CONFIG`), so one config serves bundle and container; the self-signed pair lives in `/var/lib/mcui/tls` for both (the only writable place in the container; your own certs go in `/etc/mcui/certs`). The entrypoint makes the pair (SANs: localhost, hostname, `MCUI_TLS_SAN`), runs `mcui serve`, offers `health` (https then http on loopback), else runs the CLI. **Podman Quadlet** (`mcui.container`, rootful, `Network=host`: no NAT or firewall rules on a member; ReadOnly, DropCapability=ALL, NoNewPrivileges, HealthCmd, exit 2 not restarted) is the recommended container setup and fine on members; **Compose** (`compose.yaml`) for other hosts. **Never Docker on a member** (FORWARD policy DROP breaks LXD/OVN). Found in the live run: inside an OVN instance (MTU 1442) a bridge at 1500 stalls TLS (pip timeouts; confirmed 1500 stalled, 1442 worked), so `make image` and compose build with host networking and compose has a commented MTU block; Podman's OCI format drops HEALTHCHECK, so `make image` uses `--format docker` with podman; container umask 022 left `mcui.db` 0644, so both SQLite stores now create their files 0600 (and tighten existing ones, WAL/SHM included) via `mcui.files.private_db`. Key files must still be 0600 and owned by 8843, so 0444 Docker/K8s secrets don't fit; mount files instead. `install.sh` now also creates `mcui` as uid/gid 8843 when free and resets the config's group and `certs/` owner on every run, so bundle and container can swap over the same `/etc/mcui` (seen live: a bundle user at uid 997 couldn't read a container-made config). **Verified live 2026-11-12** in a throwaway Ubuntu 24.04 VM in `mcui-test` (limits raised for the run, restored; VM and pulled image deleted): podman build 71 s, image 185 MB (60 MB saved); Quadlet start, health, `podman exec … user add`, HTTPS sign-in, uid 8843 with CapEff 0, read-only root, host network, restart keeps users, config error → exit 2 with no restarts; image-save → `docker load`; Compose build (BuildKit) and run healthy, read-only, caps dropped; plain `podman run` healthy; bundle ↔ Quadlet swap. Not published to a registry yet | Owner asked for docker/podman/Quadlet support; D16/NF-6 already planned an OCI image. One image serves all three; the Quadlet is a small unit file. | Accepted 2026-11-12 |
| D63 | **Production on Ubuntu (Phase 7a, from D16/NF-6):** `make bundle` builds `dist/mcui-<ver>-py<PYVER>-<ARCH>.tar.gz`: the mcui wheel with the built SPA inside (`mcui/static`, copied in only for the build; a checkout's `frontend/dist` still wins), every dependency as a binary wheel for the target (`pip download --platform manylinux…`, default Python 3.12/x86_64 = Ubuntu 24.04), and `deploy/` files. **Offline install:** needs only `python3`, `python3-venv`, `openssl` from Ubuntu (the site blocks the snap store; PyPI may be blocked too). `install.sh` (idempotent, root): system user `mcui`; venv per install in `/opt/mcui/<ver>-<timestamp>` with an atomically switched `current` symlink (venvs can't be moved; last 3 kept for rollback); `/etc/mcui` root:mcui 0750 with `config.toml` written once (0640) and `certs/` owned by mcui 0700 (keys must be 0600 and owned by the service user, per CF-1); data in `/var/lib/mcui`; self-signed P-256 HTTPS cert for the host's names and IPs if none; `--uninstall` keeps config and data, `--purge` removes them. **HTTPS in-process** via uvicorn (`[server] host/port/tls_cert/tls_key`, default port 8843, clear of LXD 8443/8444, MicroCloud 9443, MicroCeph 7443, MicroOVN 6443, Ceph mgr 9283, node_exporter 9100); a reverse proxy is optional and must keep `Host` (websocket Origin check). Always one worker (the collector and live metrics are in-process). **Hardened unit:** no capabilities, `ProtectSystem=strict` with only `/var/lib/mcui` writable, `@system-service` syscalls (EPERM otherwise), exit 2 (config error) not restarted. `/usr/local/bin/mcui` runs the CLI as `mcui` via `runuser` so files it creates stay the service's. `tests/test_deploy.py` checks the example config's keys (commented sections too) and that the paths agree; shellcheck in `make lint` | Owner asked for a documented production run on Ubuntu with systemd. One tarball suits sites without internet access; built-in TLS avoids a second service on a cluster member. **Verified live 2026-11-12** in a throwaway Ubuntu 24.04.5 VM in project `mcui-test` (owner-approved; `limits.virtual-machines` raised to 1 for the run and restored to 0; VM and its pulled image deleted): the documented steps (apt python3-venv, unpack, install.sh); `sudo mcui user add` (and refusal without sudo); HTTPS sign-in with the generated cert (Secure HttpOnly cookie; plain http refused); runs as mcui, ~67 MB RSS idle; file modes as documented; `systemd-analyze security` 1.5 OK; upgrade (new dir, previous kept), rollback per the guide, a 0644 key → exit 2, no restarts, reason in the journal; uninstall keeps config/data/users, reinstall picks them up; purge removes all. The LXD connection from an installed host wasn't part of the run (no keys copied into the VM). Fixed from it: bundles made on macOS now carry no xattrs and root-owned entries (GNU tar warned on every file). | Accepted 2026-11-12 |
| D62 | **Root filesystem when LXD doesn't report "/":** the VM agent lists one mountpoint per device, so a bind mount of the root disk (Podman's `/var/lib/containers/storage/overlay`) can replace `/` (seen live on a Podman VM). Then Root FS uses the instance's largest real filesystem (not tmpfs/overlay etc., not under /run, /dev, /sys, /proc, /snap, /boot) and the UI marks it with `*` naming the mountpoint (`rootfs_mountpoint`). Stored filesystem series keep mounts under /var/lib/containers and /var/lib/docker when the device is a block device (`/dev/...`), so the instance's Filesystems graph shows it too; overlay/shm mounts there are still dropped by fstype. LXD's own UI shows no disk for the same VM for a different reason: instance state `disk` is empty for a VM on Ceph | A running VM with a 75 GiB disk showed no disk. A guess that is marked beats a blank. | Accepted 2026-11-12 |
| D61 | **Unlimited instances are listed, not just counted:** `/api/hosts` returns each host's running instances that lack `limits.cpu` and/or `limits.memory` in their expanded config (`unlimited_instances`: project, name, which limit is missing; VMs never appear, LXD gives them defaults). On the Overview, a host's "N unlimited" opens a list under the Hosts table filtered to that host ("Show all hosts" widens it); each row links to the instance and to its Configuration tab (`?tab=config`), where the limit fields are. Only running instances, matching Allocated | Unlimited containers can take a whole host and make Allocated understate it; the count alone didn't say which ones. Limits may equally go on a profile, which the note says. | Accepted 2026-11-12 |
| D60 | **Theme switch (Light / Dark / System):** colours are CSS variables on `:root` (incl. `--series-0..7` for chart lines and `--ok`); dark values apply under `prefers-color-scheme: dark` unless `<html data-theme="light">`, or under `data-theme="dark"`. A top-bar select sets `data-theme` (none = System) and remembers it in `localStorage` (`mcui.theme`; storage blocked = this page only); it's applied in `main.tsx` before the first render. Charts read their colours from CSS when drawn and redraw when the shown theme changes (OS switch or user choice). Dark series colours are brighter variants of the light ones. Checked every screenshot page dark. **README screenshots are dark** (the owner's choice: it suits the Ubuntu orange), plus one light Overview; `make screenshots` produces exactly that set | Follows the OS by default; an explicit override for those who want one. | Accepted 2026-11-11 |
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
- **Phase 3c (create instance): built 2026-11-05.** `/instances/new` wizard:
  image picker (local, `images:`, `ubuntu:`; D47), project, member or `@group`,
  profiles, CPU/memory, root pool/size, NICs (managed networks, static IPv4),
  extra volume disks, cloud-init, security/boot, start; request preview. Live in
  `mcui-test`: created from a local image (with placement, root size, limits,
  cloud-init) and from `images:alpine/edge`.
- **Phase 3d (consoles): built 2026-11-05.** Terminal (exec, xterm.js 6.0.0),
  text console, VM graphical console (spice-html5 0.2.1, Ctrl+Alt+Del, full
  screen), console log view/clear; backend streams (D48). Live (Python websocket
  client standing in for the browser): exec on a container on another member,
  window resize, clean exit; force delete after exec + console now works (it
  failed before D48); long creates no longer misreported (D49).
  Text console confirmed by the owner 2026-11-11 on Ubuntu containers and VMs;
  the Alpine/Debian sandbox containers send nothing to their console even
  attached directly to LXD (an image trait, not an mcui bug). **Open:** **VGA not
  live-tested** (sandbox allows no VMs); xterm/SPICE in a real browser untested.
- **Phase 3e (file browser): built 2026-11-05.** Files tab: path box, breadcrumbs,
  typed listing with size/mode/owner/mtime, folders and symlinks navigable
  (follow), downloads, upload (multiple files), new folder, delete (D50).
- **Phase 3f (profiles, images, projects): built 2026-11-05.** Profiles list/
  detail (form + YAML, used-by links, copy, rename, delete), Images (aliases add/
  remove, edit description/auto-update/public, delete, import from `images:`/
  `ubuntu:`, upload a unified tarball), Projects list/detail (features on create,
  config YAML, rename, delete) (D51). Live in the sandbox: profile CRUD, image
  alias/edit/import/delete, project update.
- **Phase 3 is built.** What still needs the owner live (browser, VM, cluster-wide
  actions): `docs/LIVE-TESTS.md`.
- **Phase 4a (networks): built 2026-11-11.** Network list (managed, with type summary and used-by; each member's own interfaces), detail (status, members, cluster-wide config YAML, per-member settings, state per member, allocations, DHCP leases, used-by links), create form for bridge/OVN/physical/macvlan with per-member parents, delete (D52). Live on the real cluster (D41 extension): created bridge `mcui-test-br` (10.254.254.1/24, no NAT, no IPv6) on all three members, state per member, edits incl. a per-member save (found and fixed: it blanked the description), a sandbox container attached, delete refused while in use, then everything deleted. OVN/physical create left to the owner (LIVE-TESTS N1–N3).
- **Phase 4b (ACLs, forwards): built 2026-11-11.** ACL list/detail (rule editor, rename, delete), ACL assignment and default actions on bridge/OVN network pages, forwards add/edit/delete with ports (D53). Live on the real cluster: ACL `mcui-test-acl` create/edit/attach to bridge `mcui-test-br`/rename and delete refused while in use; forwards from 192.0.2.10/.11 (with and without a member) create/edit/delete; all removed afterwards. Found live: bridge ACLs reject `@internal`/`@external` (help text updated).
- **Phase 4c (storage pools): built 2026-11-11.** Pool list on the Storage page, pool page (capacity per member, history, settings, per-member settings, used-by, delete), create form for zfs/lvm/btrfs/dir (per-member source/size, shared pool/VG names) and ceph/cephfs (D54). Live read-only on the real cluster (Ceph RBD and CephFS pools). **Pending:** local-pool create/edit/delete and fixtures on the throwaway LXD (`mcui-lxd` VM in `mcui-test`, LXD 5.21.3 being built from source because the snap store is unreachable from the owner's network).
- **Phase 4c live on the throwaway LXD (2026-11-11):** zfs/btrfs/lvm pools created through mcui with the per-member step (target `tl1`), a bad source refused and cleaned up, cluster-wide and per-member edits (a member save needs all its keys), delete refused while in use; fixtures recorded (`recorded-local`).
- **Phase 4d (volumes): built 2026-11-11.** Volume list/create (pool, member for local pools, content type, size), volume page (usage, resize, snapshot schedule, snapshots, attach/detach, rename, delete) (D55). Live: in `mcui-test` on Ceph RBD (create, resize + schedule, snapshots with expiry, rename, restore, attach to a container, delete refused while attached, detach, rename, delete) and on the throwaway LXD's zfs/lvm pools (with `target`). Found live and fixed: config PUTs dropped `volatile.*` keys; failed operations came back as 502s; snapshots were listed as volumes.
- **Phase 4a, more live (2026-11-11):** in the throwaway LXD, physical and macvlan networks with per-member parents (on dummy interfaces), parent change, missing parent refused; bridge leases and allocations for four containers.
- **Phase 4e (Ceph dashboard): built 2026-11-11.** `/ceph` page: health and checks, mons/mgr, OSD table, PG states, raw capacity, pools, history charts, busiest RBD images linked to instances/volumes (D56). Live against the real ceph-mgr: HEALTH_WARN (`MON_DISK_LOW`, `BLUESTORE_FREE_FRAGMENTATION`), 6 OSDs, 193 PGs active+clean; `lxd_remote` 86% used (47.6 GB available), osd.2 84% (near Ceph's 85% nearfull); RBD top mapped across projects.
- **Phase 4f (OVN view): built 2026-11-11.** `/ovn` page (linked from Networks): uplinks with router-address use, OVN networks with chassis, router address, forwards/LBs/peers, ACLs, allocations, and a problems list (D57). Live: 3 OVN networks on `UPLINK` (3 of 205 router addresses), chassis spread over the three members.
- **Phase 4 is built** (4a–4f). Owner live checks: `docs/LIVE-TESTS.md` (N, S, E, O sections).
- **Phase 4g (hosts): built 2026-11-11** at the owner's request, bringing node_exporter (MO-12) forward from Phase 5: Overview Hosts table, node_exporter scrape/history, member-page host graphs, `mcui check` section, `docs/HOST-METRICS.md` (D58). Live (plain http, the Ubuntu package as installed): all three hosts scraped, 42 series kept per host of ~3,300 (sda/sdb only; dm-*, rbd* and their partitions left out; `/`, `/boot`, `/boot/efi`); the zfs module reports 1,920 bytes of ARC with no pool, so ARC under 1 MiB is hidden; recorded fixtures in `recorded-node`. Owner checks: LIVE-TESTS H1–H4.
- **README screenshots (2026-11-11):** `make screenshots` from the demo cluster (D59); `formatNumber` now prints whole numbers without decimals (Ceph object counts showed “2.00”).
- **Theme switch (2026-11-11):** Light / Dark / System in the top bar; charts follow it; all pages checked dark (D60).
- **Unlimited instances (2026-11-12):** the Hosts table's "N unlimited" opens a list of those instances with links to set limits (D61).
- **Root FS for Podman VMs (2026-11-12):** falls back to the largest real filesystem when "/" isn't reported, marked `*` (D62).
- **Production deploy (2026-11-12):** `make bundle` + `deploy/install.sh` + hardened systemd unit + built-in HTTPS; `docs/DEPLOY.md` (D63); install lifecycle verified in an Ubuntu 24.04 VM.
- **Container image (2026-11-12):** `make image`, Podman Quadlet (recommended, also on members) and Compose (not on members); DEPLOY.md §9 (D64).
- **Reverse proxies (2026-11-12):** DEPLOY.md §10 (NGINX Proxy Manager, Cosmos, Caddy, nginx); `public_url`, `forwarded_allow_ips` (D65).
- **Release workflow 2026-11-12 (D67):** tag `vX.Y.Z` → GHCR image (amd64+arm64, native runners) +
  GitHub Release with bundles; docs/RELEASING.md. **Dry run passed 2026-11-12** (run
  43240796585): CI, both images natively (arm64 on an aarch64 runner, 97 s, `push=false`),
  four bundles. amd64 image also run-tested in a throwaway VM (health, uid 8843,
  healthcheck).
- **0.1.2 released 2026-11-12** (run 43262784553): the move to Ubuntu 26.04 (D72), called
  out in a headline from the annotated tag's message. `:latest` is on 26.04 (both
  architectures). `main` is now 0.1.3.dev0.
- **Ubuntu 26.04 2026-11-12 (D72):** py3.14 bundles, image on `ubuntu:26.04`; both
  verified in a 26.04 VM. The arm64 26.04 image is first built by the next edge run.
- **Release and edge notes list the commits since the previous/latest release**
  (`build_version.py changes`, version bumps left out; D67/D68).
- **0.1.1 released 2026-11-12** (run 43254431150): D69–D71 (table alignment, Hosts table
  fits 1280px, config fallback). `main` is now 0.1.2.dev0.
- **`mcui` finds `~/.config/mcui/config.toml` by itself 2026-11-12 (D71);** README dev
  instructions corrected.
- **Hosts table compacted to fit 1280px windows 2026-11-12 (D70).**
- **Table headers aligned with numeric columns 2026-11-12 (D69);** screenshots for the
  affected pages regenerated.
- **Edge channel 2026-11-12 (D68):** every push to `main` that passes CI publishes
  `:edge`, `:edge-<sha>` and the pre-release `edge` (bundles), version `X.Y.Z.devN`.
  First edge build passed on GitHub (run 43245626348, `05eefb0`, 0.1.1.dev2); the
  following push checks the update path (tag moved, bundles replaced).
- **0.1.0 released 2026-11-12** (run 43242283224): GitHub Release with the four bundles;
  `ghcr.io/moon127/microcloud-ui` `0.1.0`, `0.1`, `latest` (amd64 + arm64), public and
  pullable anonymously. `main` is now 0.1.1.dev0. Not yet done: *running* the arm64 image
  on an ARM host. 22.04 (py3.10) bundles now include `tomli`.
- **Git history scrubbed 2026-11-12 (D66):** no `Claude-Session:` trailers; commit-msg hook
  guards against them.
- **0.1.4 released 2026-11-12** (run 43292179897): the Phase 5 LXD 6.10 features (placement groups,
  image registries, OVN load balancers, multi-volume snapshots, child operations, forced evacuation)
  and the boot.mode fix. A first tag had to be deleted (the release commit failed a flaky test and
  the script tagged anyway; the version check stopped the run, nothing was published). `main` is
  now 0.1.5.dev0.
- **Phase 7b done 2026-11-13 (D83):** custom volume backups: a Backups section on the volume
  page (the panel shared with instances, `src/Backups.tsx`) and Volumes → Import backup (pool,
  member for local pools, name). Volume snapshot buttons now follow the operate right, as the
  server already did. Live in `mcui-test` through the app (13 checks): refused until
  `restricted.backups=allow`, operator makes/downloads (zstd, named)/renames/deletes, viewer
  only lists, a project admin imports it into the restricted project (volumes don't hit 7a's
  `volatile.*` refusal). Next: 7d, ISO upload; 7c waits for a second project.
- **Phase 7a done 2026-11-13 (D83):** instance backups: a Backups tab (list; make with
  expiry, compression, without snapshots, optimized; rename; delete; download streamed and
  named by its compression) and Instances → Import backup (streamed upload with progress in
  the toast; `run` passes start a progress callback). Live in `mcui-test` through the app:
  operator makes, renames and downloads, viewer only lists, operator can't import, other
  projects 404, refused until `restricted.backups=allow`. Found live: LXD 6.10.1 refuses
  importing a backup into any project with `limits.*` or `restricted` (its `volatile.*`
  keys count as low-level) unless `restricted.containers.lowlevel=allow`; a crafted
  privileged backup was still refused with that allowed. Unrestricted + lowlevel=allow
  imported it with its files. The dialog warns; USERS.md says so. Next: 7b.
- **Phase 7 agreed 2026-11-13 (D83):** backups, project moves, ISO upload and restriction
  keys (`docs/PLAN.md`). Found first, live: restricted projects refuse snapshots and backups
  by default. Next: 7a, instance backups.
- **0.2.1 released 2026-11-13:** the Audit page and the security model docs (D82). The
  README status was rewritten afterwards (it still said "Phases 1–4"). `main` is now
  0.2.2.dev0; development is parked for the weekend while the owner exercises users and
  projects on the cluster.
- **Audit page and security model docs 2026-11-13 (D82).**
- **0.2.0 released 2026-11-13:**
  - **New:** users limited to LXD projects (Phase 6, D81, `docs/USERS.md`).
  - **Changed for existing users:** cluster viewers lose Console/Files and gain the audit
    log.
  - `main` is now 0.2.1.dev0.
  - **Next small idea:** an Audit page in the UI (the log is API-only today).
- **Phase 6 done 2026-11-13 (D81):** 6e added `docs/USERS.md`, the README and requirements
  (AU-5), the Users screenshot, and a Restricted option when creating a project. Ready for
  release 0.2.0.
- **Phase 6d done 2026-11-13 (D81):** live on the test cloud (`mcui-test` restricted for
  the run, `default` as the other project, read-only). Found that LXD's operations and
  allocations name their project only in URLs (`mcui/lxd/entities.py`). Next: 6e docs,
  then 0.2.0.
- **Phase 6c done 2026-11-13 (D81):** the UI follows the user's rights (`access.ts`), with
  a Users page and navigation and pages by role. Next: 6d live checks on the cluster,
  which the owner asked for.
- **Phase 6b done 2026-11-13 (D81):**
  - **Filtering:** lists, operations, metrics, audit and events are filtered for project
    users; streams are owner-only.
  - **The lockout is lifted:** project users can sign in.
  - **Audit:** global viewers still can't read it, as before; that's for the owner to
    review.
  - **Next, 6c:** the UI.
- **Phase 6a done 2026-11-13 (D81):**
  - **Users:** project users and grants (store migration, API, CLI).
  - **Enforcement:** a declared policy on every route, checked by a test.
  - **Behaviour change:** files and consoles now need operate, not just read.
  - **Next, 6b:** project users stay locked out until the server filters lists,
    metrics, operations, audit and the event stream, and checks stream ownership.
- **React Compiler lint rules on 2026-11-13:** the 12 places rewritten (edit pages and the
  YAML editor take loaded values during render; `useEffectEvent` in `events.ts` and the
  terminal; the consoles reset on Reconnect). This also removes the cause of the
  project-page test race. Owner tested the edit pages and consoles locally; no release
  yet (waits for the next feature).
- **Phase 5 done 2026-11-13:** the last compatibility items checked against the cluster
  recording: metric names and memory, removed settings and the `control-plane` role,
  node_exporter on 26.04, Ceph RBD stats, and every change following its operation. None
  needed a code change.
- **0.1.10 released 2026-11-13:** bulk actions under one toast (D78), OSD % used against
  nearfull/full (D79), the frontend upgrades. `main` is now 0.1.11.dev0.
- **Bulk actions under one toast 2026-11-13 (D78)**; owner-tested on the edge build.
- **OSD % used with nearfull/full thresholds 2026-11-13 (D79).**
- **Frontend upgrades 2026-11-13:**
  - TypeScript 6.0, not 7: `typescript-eslint` supports TypeScript below 6.1 only. The app
    now targets ES2022.
  - Vite 8.3.5, Vitest 5.0.5, ESLint 10 and React Router 8.5.1. Each was the newest
    release at least two weeks old.
  - The React Hooks plugin 7's compiler rules are off: they flag 12 places, a follow-up in
    the plan.
  - A new test keeps `THIRD_PARTY_NOTICES.md` in step with the pinned runtime packages.
  - The only audit finding left is a moderate `minimatch` advisory, in lint tooling only.
- **0.1.9 released 2026-11-13:** Ubuntu 22.04 dropped (D77); four bundles. `main` is now
  0.1.10.dev0.
- **Ubuntu 22.04 dropped 2026-11-13 (D77):** Python 3.12 minimum, four bundles, CI on 3.12
  and 3.14. Also fixed a timing race in the project-page test (the form loads its values in
  an effect after the first render).
- **0.1.8 released 2026-11-13:** vulnerability watching (D76): `make audit` in CI and so
  before releases, the weekly image scan, Dependabot (majors skipped), no `pip` in the image,
  and GitHub Actions on their current majors. `main` is now 0.1.9.dev0.
- **Vulnerability watching 2026-11-13 (D76):** Dependabot alerts and security updates on;
  `dependabot.yml`; `make audit` in CI; weekly `security.yml` with a Trivy image scan and
  a `rebuild-needed` issue. First audit clean for what ships; 7 moderate in frontend build
  tools (typescript-eslint chain), below the bar.
- **0.1.7 released 2026-11-13:** evacuation warnings and progress (D75), the C2a findings
  and the mixed-CPU advice. `main` is now 0.1.8.dev0.
- **Evacuation warnings and progress 2026-11-13 (D75):** after the C2a test (LXD let a second
  member of three be evacuated, and two restores failed on mixed CPUs). Fixture-tested;
  the owner checks it live (`docs/LIVE-TESTS.md` C2b).
- **0.1.6 released 2026-11-13:** documentation only. The README and DEPLOY say MicroCloud
  2.x and 3.4 are both fully supported and list what 2.x doesn't show; the screenshots
  come from a 3.4 demo cluster (D59 amended), with placement groups and load balancers
  added. The bundles carry the updated DEPLOY.md. `main` is now 0.1.7.dev0.
- **0.1.5 released 2026-11-13** (run 43396165721): checked live on the test cloud after its
  upgrade to MicroCloud 3.4 (placement, registries, load balancers, multi-volume snapshots,
  child operations, evacuate/restore), the evacuation "auto" fix, OVN DHCP ranges. A first tag
  went to the wrong commit again: `git commit -a` gave the pre-commit hook a temporary
  GIT_INDEX_FILE that broke the release tests (fixed), and a `grep` pipe hid the failure. The
  version check stopped the run, nothing was published, and the owner deleted the tag. `main`
  is now 0.1.6.dev0.
- **Phase 5b, image registries 2026-11-12 (D47 amended):** picker uses LXD 6.10's registries
  (built-in and custom), instances from `source.image_registry`, registry list/add/delete on the Images
  page; LXD 5.21 unchanged. Verified on the 6.10 VM. With this, the agreed 5b scope is built.
- **Phase 5b, OVN load balancers and multi-volume snapshots 2026-11-12:** see PLAN.md 5b; verified
  through the app against the 6.10 VM (LB allocated from the uplink, ports to pools or backends,
  pool health and live target status; snapshot/restore with exclusive volumes).
- **Phase 5a/5b on the VM 2026-11-12:** operations page uses `entity_url` and nests bulk children;
  forced evacuation offered after LXD 6.9's quorum refusal; VM secure boot off is `boot.mode` on
  6.7+ (`boot_mode` adapt; 6.10 refuses `security.secureboot`); placement groups page + wizard
  choice. All verified through the app against the 6.10 VM.
- **0.1.3 released 2026-11-12** (run 43283773241): CephFS pools on LXD 6.8+, the Ceph page without
  Tentacle perf counters, `mcui check` release/security warnings. `main` is now 0.1.4.dev0. The
  MicroCloud 3.4 VM `mcui-test-mc34` was kept for Phase 5 work (owner, 2026-11-12) and deleted
  on 2026-11-13 with its image, once the test cloud itself ran 3.4; `mcui-test` limits are back
  to 0 VMs, 2 CPU, 1 GiB, 6 GiB disk, 3 instances.
- **Phase 5.1, MicroCloud 3.4 VM 2026-11-12 (D74):** the app works against LXD 6.10 nearly
  unchanged (reads and background-operation writes verified); CephFS pool create fixed
  (`cephfs.path`); Ceph page hides OSD used/size and per-op latency without perf counters;
  `recorded-6.10` fixtures (placement groups, image registries, LB pools, multi-volume
  snapshot, bulk parent/child operation, Tentacle mgr both ways).
- **Phase 5a started 2026-11-12 (owner's go-ahead):** `mcui check` labels each member's LXD release
  (LTS series and support end), warns about releases missing the 2026-10-28 TLS security fix
  (5.21.9 / 6.10.1 / 5.0.11) and about members on different versions; warnings, not failures.
- **Phase 5.0: LXD 5.21 baseline recorded 2026-11-12** (`tests/fixtures/recorded-5.21`, 55 files;
  `record-fixtures` now covers everything the app reads, Ceph mgr and node_exporter included).
- Next: **Phase 5, MicroCloud 3.4 LTS** (agreed 2026-11-12, `docs/PLAN.md`, D73): starts after the
  owner's cluster upgrade. Then Phase 6 (metric store,
  alerts, export, quota history; was Phase 5).
  (incl. local-pool fixtures, see Known gaps).

## Working conventions

- `make lint test` must pass before every commit (`make hooks` installs it as
  the pre-commit hook, which also checks the commit email; it also installs the
  commit-msg hook that rejects session URLs, D66).
- Sessions now run on the owner's Mac (`.venv`, Node 22; no lxc/docker/podman). Live
  LXD work goes through `scripts/lxd_api.py` with the dev certs; Linux-only checks use
  throwaway VMs in `mcui-test` (see CLAUDE.md for the rules). If a session ever runs on
  a Linux VM against this checkout, keep its venv outside the repo
  (`make … VENV=$HOME/mcui-venv`) and delete `frontend/node_modules` afterwards, so no
  Linux binaries are left in the macOS checkout.
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
- **Throwaway LXD (Phase 4, D41 extension):** VM `mcui-lxd` in `mcui-test` (2 vCPU, 2 GiB, 25 GiB root on `remote`, NIC on OVN `default`); the project's limits were raised for it (1 VM, 4 instances, 4 CPU, 3 GiB, 40 GiB). **Deleted 2026-11-11** with its Ubuntu VM image; the limits are back to the values below. To redo: same VM, then `build.sh`-style source build (see the Phase 4c/4d notes). IPv6 egress from OVN `default` doesn't work and `api.snapcraft.io` is unreachable (also unresolvable from the owner's Mac), so LXD is built from the 5.21.3 release tarball inside the VM.
- **Sandbox project `mcui-test`** (D41), created 2026-11-05: own images, profiles and volumes (`features.images/profiles/storage.volumes=true`), shared networks; default profile has a 2 GiB root on `remote` and **no NIC**, `limits.cpu=1`, `limits.memory=256MiB`; project limits 3 containers, 0 VMs, 2 CPU, 1 GiB RAM, 6 GiB disk. Remove with: delete its instances and images, then `lxc project delete mcui-test`.
- LXD's filesystem metrics may omit a VM's `/` mount (seen on podman-1, which bind-mounts its root disk for Podman). Root FS then falls back to the largest real filesystem, marked `*` (D62).

## Known gaps (not testable on the test cloud)

The test cloud only has Ceph-backed storage and one network setup. Close each
gap with recorded fixtures from a cluster (or a throwaway LXD VM) that has it.

| Gap | Risk | Status |
|---|---|---|
| **Local storage pools** (zfs, dir, lvm, btrfs) | Closed 2026-11-11 with fixtures from a throwaway LXD 5.21.3 (`tests/fixtures/recorded-local`, `test_recorded_local.py`): member state lists every local pool (capacity history works); a container on **lvm** repeats its I/O on the stacked `dm-*` layers and the disk, so D40 counts it once; btrfs/dir I/O is charged to the disk. Pool/volume management tested live in that LXD. **Remaining:** ZFS attributes no I/O to containers (ZFS writes from kernel threads), so their disk graphs stay at zero; VMs on local pools (no nested KVM) untested | Fixture-tested + live in the throwaway LXD |
| **Standalone (non-clustered) LXD** | Member state is skipped on 404 (no host load/pool usage); everything else uses the single endpoint | Fixture-tested only |
| **Other network types** (bridge-managed, macvlan, sriov) | Instance-NIC mapping by MAC (D39) should work; host-NIC collection filters by type | Untested live |

## Repo layout

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
  mcui/files.py     private (0600) SQLite files
frontend/           React + TS + Vite SPA
  screenshots/      invented demo cluster + Playwright shooter (make screenshots, D59)
deploy/             production: install.sh, mcui.service, mcui-cli, config.example.toml (D63)
  container/        Containerfile, entrypoint.sh, Podman Quadlet, compose.yaml (D64)
docs/PLAN.md        phased delivery plan
docs/DEPLOY.md      production guide (bundle + systemd, containers)
docs/LIVE-TESTS.md  checks only the owner can run
docs/HOST-METRICS.md node_exporter install and lock-down
Makefile            dev tasks (make help)
scripts/pre-commit  lint+test+email gate
scripts/commit-msg  rejects Claude session URLs in messages (D66)
scripts/build_version.py  build versions: release tag check, X.Y.Z.devN for edge (D67, D68)
.github/workflows/  ci.yml (every push), release.yml (tags vX.Y.Z; edge after CI on main, D67/D68)
docs/RELEASING.md   how to cut a release
scripts/bundle.sh   offline Ubuntu bundle (make bundle)
scripts/lxd_api.py  dev aid: LXD API calls/exec/push with the dev certs
CLAUDE.md           session brief: read-first list, hard rules, environment, gotchas
docs/REQUIREMENTS.md  requirement IDs and tiers
docs/lxd-api-surface.md  LXD REST path reference
docs/CONFIGURATION.md    config reference (tool side)
docs/LXD-CREDENTIALS.md  certs + LXD-side setup
docs/CEPH-METRICS.md     MicroCeph mgr prometheus setup
```
