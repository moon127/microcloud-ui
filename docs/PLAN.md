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

**Phase 5: MicroCloud 3.4 LTS. ✅ Done 2026-11-13** (agreed 2026-11-12). The app fully
supports MicroCloud 3.4 LTS and still works with MicroCloud 2.x. Checked live on the
upgraded test cloud. The last compatibility items were checked against its recording
(`recorded-6.10-cluster`) and needed no code changes.

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
    may leave the Ceph managers unable to start. The test cloud runs 19.2.3, and
    MicroCeph's `tentacle/stable` was 20.2.1 on 2026-11-12, so read the upgrade guide's
    Ceph part closely.
  - LXD 6.10 lists a known issue: during MicroCeph upgrades, don't rotate keys until
    LXD's Ceph client supports the new key type.
  - snapd 2.75 or later on every member.
- **Upgraded 2026-11-12** to LXD 6.10.1, MicroCeph Tentacle 20.2.1, MicroOVN 26.03 and
  MicroCloud 3.4.1 on Ubuntu 26.04. After the reboots OVS stayed in `flow-restore-wait` on
  every member (no OVN traffic at all) until the owner cleared it (`docs/DEPLOY.md`).
- ✅ **Recorded the cluster on 6.10** (2026-11-13): `backend/tests/fixtures/recorded-6.10-cluster/`
  (64 files), the same cluster as `recorded-5.21`. The Ceph perf-counter setting (D23)
  survived the upgrade, so the Tentacle hint isn't needed there.
- ✅ **Multi-member checks on the cluster** (2026-11-13, through the app's API in
  `mcui-test`):
  - **Placement:** a strict `spread` group placed three containers on three different
    members.
  - **Image registries:** those containers were created from the `images` registry.
  - **Moves:** a move to a named member that already held one of the group went ahead.
    LXD applies a group only when it picks the member, and the placement page now says so.
  - **Multi-volume snapshots:** a snapshot of a container with a Ceph custom volume
    attached (`all-exclusive`) also made `v/snap0`. Restores worked both with and without
    the volumes.
  - **Child operations:** a bulk stop, start and restart across the members gave a parent
    with `child_count` 4. Each child's `metadata.entity_url` names its instance, and each
    has the coordinating member as `location`, whichever member runs the instance.

  - **OVN load balancers** (on `legacy-infra`, owner's go-ahead; two `mcui-test` containers
    on it as backends):
    - A load balancer with an allocated address got one from the uplink's routed range,
      not its OVN range, and answered from the Mac, alternating between both backends.
    - Editing it down to one backend through the app took effect at once.
    - LXD refuses a pool whose instances are in another project ("does not exist in
      project default"). The pool form now labels the field with the network's project.
      Pools with health checks remain verified on the 6.10 VM only.

  - **Evacuation** (owner, member 2): LXD 6.10 refused our "auto" mode ("Invalid
    evacuation mode") after it had already marked the member evacuated and demoted it from
    voter; no instance moved, and Restore put it back. "auto" is only an instance's
    `cluster.evacuate` value; the evacuate call takes stop, migrate or live-migrate. The
    dialog no longer offers it, and the API refuses it.

  - **Evacuation and restore with the fix** (owner, member 2, Default mode):
    - The evacuation moved four instances in two minutes. The OVN gateway moved off the
      member, and the database ran on the other two voters.
    - The first restore failed. A VM with `migration.stateful=true`, which had
      live-migrated out fine, failed to live-migrate back ("Failed restoring checkpoint").
      LXD stopped it and left the member evacuated.
    - With the VM stopped, a second restore moved everything back. The VM stayed stopped,
      as LXD doesn't start a stopped instance it moves.
    - The cause is mixed CPUs. Members 1 and 2 are Xeon D-1518 (Broadwell); member 3 is
      Xeon D-2123IT (Skylake, AVX-512). QEMU logged "Failed to set XSAVE: Invalid
      argument". The workaround is `cluster.evacuate=migrate` on stateful VMs
      (`docs/LIVE-TESTS.md` C1/C2).
    - Possible later feature: warn in the evacuate dialog when stateful VMs would
      live-migrate between members with different CPU models. The members' resources
      already give the models.

  - **The quorum refusal (C2a):** LXD 6.10 did **not** refuse evacuating a second
    member of three. The first one's demotion left two voters, and LXD counts one of two
    as a majority. All instances ended up on one member, and the restore failed on the
    CPU difference until the VMs were stopped. Full account: `docs/LIVE-TESTS.md`.
    - ✅ **Follow-up (5c, D75):**
      - The evacuate dialog warns when another member is out, and when VMs would
        live-migrate between different CPUs.
      - The restore dialog names the VMs to stop first.
      - Evacuations and restores show LXD's progress and the member's instances as they
        go.
      - Fixture-tested; live check C2b. LXD's progress metadata key is assumed to end in
        `progress`, as LXD's other progress keys do; C2b confirms it.
- **After:** `mcui check`, the integration tests, and `docs/LIVE-TESTS.md`. This is
  "verify the current code still works"; anything that fails feeds 5a.

**5.1 Seeded from a "MicroCloud 3.4 in a box" VM. ✅ 2026-11-12 (D74).** A throwaway Ubuntu
26.04 VM in `mcui-test` with the 3.4 component snaps (LXD 6.10.1, MicroCeph 20.2.1
Tentacle, MicroOVN 26.03, MicroCloud 3.4.1), wired as MicroCloud does, with throwaway
certificates. Findings:
- **The current app works against LXD 6.10 nearly unchanged:** `mcui check` passes, all
  38 read endpoints answer, and creates, updates, renames and deletes of ACLs, volumes,
  volume snapshots, profiles, networks and OVN forwards complete through LXD's new
  background operations (`operations.start`/`wait` already handled them).
- **Broken on 6.10, fixed:** creating a CephFS pool (LXD refuses `source`); the form
  now sends `cephfs.path` when the server has `storage_remote_drop_source`.
- **Ceph Tentacle:** without `exclude_perf_counters=false` (and a mgr restart), OSD
  used/size and per-op latency are missing; the Ceph page now hides them with a hint
  (`docs/CEPH-METRICS.md`).
- **Operations:** every one carries `entity_url`, and `resources` is empty. A bulk state
  change on `/1.0/instances` makes a parent with `child_count` and nested children; the
  CLI's `lxc stop a b c` makes separate operations.
- **New objects recorded** (`tests/fixtures/recorded-6.10`): placement groups, image
  registries (with built-ins: `images`, `ubuntu`, `ubuntu-daily`, minimal), OVN
  load-balancer pools with health checks behind an automatically addressed load
  balancer, a multi-volume snapshot. `placement.group` accepts a group that doesn't exist.
- **LXD quirk:** creating an OVN load balancer failed (`invalid CIDR address: none/32`)
  while the uplink bridge had `ipv6.address: none`.

**5a Compatibility (must have).** One commit per item, each tested on fixtures from both
LTS releases and checked live on 3.4.
- **Two fixture sets** (both recorded): `recorded-5.21` (from 5.0) and `recorded-6.10` (from 5.1), with the
  recorded-fixture tests run against both. ✅ The demo cluster for screenshots (D59) runs
  LXD 6.10 (2026-11-13), with placement groups, registries and load balancers shown.
- **Background operations** (verified working on 6.10, 5.1): LXD 6.6 and 6.8 made create, update, rename and delete
  return background operations for storage pools, volumes, buckets, networks, ACLs,
  forwards, load balancers, peers, zones, and profile PUT/PATCH; project delete followed
  in 6.7. The backend (`operations.start`) and the frontend (`actions.ts`) already follow
  operations, so this is mainly checking every form waits for the result and shows its
  errors. ✅ Checked 2026-11-13: every change call in the frontend goes through `run` or
  `runAll` (including the member page's `follow` and the Instances page's `bulk`), which
  follow the operation to its end and report its error.
- ✅ **Operation targets:** an operation's `resources` is now empty; the target is in
  `entity_url` (and `original_entity_url` after a rename). The operations page, toasts
  and audit log should use it. Show child operations (`recursion=2`, `child_count`).
- **Renamed and removed settings:**
  - ✅ CephFS pools take `cephfs.path`, not `source` (5.1). Ceph RBD pools use
    `ceph.osd.pool_name`, which the form already does.
  - ✅ `boot.mode` replaces `security.secureboot`, which LXD 6.7+ refuses for VMs: the
    wizard's "Secure boot" off sends `boot.mode=uefi-nosecureboot` there
    (`instance_boot_mode`), `security.secureboot=false` on 5.21. VM disks use virtiofs
    instead of 9p (nothing in the UI sets 9p).
  - ✅ Gone: `cluster.healing_threshold`, `nvidia.*`, `maas.*` and `fan.type=ipip`; nothing
    in the app sets them (checked 2026-11-13).
  - ✅ The `event-hub` member role becomes `control-plane`: roles are shown as LXD gives
    them, and only the evacuation warning reads them (it looks for `database*`).
- ✅ **Evacuate and restore:** LXD 6.9 refuses to evacuate a member when that would break
  the database quorum, unless forced (`clustering_evacuation_force`). Restore conflicts
  with a running evacuation. Show the refusal and offer "force" with a warning
  (fixture-tested; the owner runs it, D41).
- ✅ **Metrics** (checked on `recorded-6.10-cluster`, 2026-11-13):
  - Every `/1.0/metrics` name the app uses is still exported. 6.10 adds
    `lxd_memory_SReclaimable_bytes` and `lxd_replicators`; none were removed.
  - Instance memory used is total minus available. Since 6.9 "available" counts
    reclaimable slab, so "used" is slightly lower and closer to what `free` shows.
    Sensible on the cluster, so no change.
- **Ceph Tentacle:** the mgr Prometheus module no longer exports daemon perf counters by
  default; `ceph-exporter` does. So the OSD apply/commit and per-op latency graphs
  (MO-7a) go blank unless `mgr/prometheus/exclude_perf_counters` is set to false (one
  `ceph config set`, owner's call; MicroCeph runs no `ceph-exporter`, see below). Hide
  those views with a hint when the counters are missing.
  ✅ Checked on the live cluster (2026-11-13): the eight `ceph_rbd_*` series are as on
  Squid. Every one of its 41 RBD images maps to its LXD owner (17 instances, 23 image
  volumes, 1 other). Tentacle adds `ceph_rbd_image_metadata`, which isn't needed.
- ✅ **`mcui check`:** each member's LXD version and LTS track, a warning for mixed
  versions and for releases missing the October 2026 TLS fix (5.21.8 today).
- ✅ **Ceph page without perf counters** (5.1): hides OSD used/size and per-op latency with
  the fix.
- ✅ **Hosts on Ubuntu 26.04:** node_exporter 1.10.2 from 26.04 fills the same 17 host
  fields as 1.7.0 on 24.04, and exports all 26 `node_*` metrics the app uses.

**5b What LXD 6 adds** (each behind its `api_extensions` flag). The owner chose
(2026-11-12) placement groups, image registries, OVN load balancers, child operations
and multi-volume snapshots for Phase 5:
- ✅ **Placement groups** (`instance_placement_groups`): a page (from Instances) to list,
  create, edit and delete them, and a "Placement group" choice in the create wizard
  (`placement.group`); LXD before 6.6 gets an explanation.
- ✅ **Image registries** (`image_registries`, LXD 6.10+): the create-instance picker
  offers the server's registries (built-in and custom) and lists their images through
  LXD (`/1.0/image-registries/{name}/images`, newest serial per product, cached like
  D47); instances are created with `source.image_registry`. The Images page lists
  registries and lets admins add (an HTTPS SimpleStreams URL) and delete custom ones.
  On LXD 5.21 the picker keeps `images:`/`ubuntu:` read by the backend (D47). Not yet:
  registries that point at an LXD project or cluster link, `restricted.registries`, and
  importing (IM-2) from a custom registry.
- ✅ **OVN load balancers** (from NW-4/7) on the OVN network page: list, add (an
  address, or one allocated from the uplink: `listen_address` `0.0.0.0`), edit and
  delete; ports send to named backends or (LXD 6.9+) a pool; pools with instances,
  target port and (6.10+) a health check, each target's live status from the pool's
  `/state`. Not yet: internal load balancers and forwards.
- ✅ **OVN DHCP ranges** (`ovn_dhcp_ranges`, LXD 6.6+): an "IPv4 DHCP ranges" field when
  creating an OVN network, shown only where the server supports it.
- ✅ **Child operations:** a bulk operation's children nested under it on the operations
  page (`recursion=2`, `child_count`).
- ✅ **Multi-volume snapshots** (`instance_snapshots_multi_volume`): "include volumes
  attached only to this instance" when taking a snapshot and when restoring one
  (`disk_volumes_mode: all-exclusive`). LXD records no link back from the instance
  snapshot; each volume snapshot is described "Created alongside container …", and
  deleting the instance snapshot keeps them, which the delete dialog says.

Not in Phase 5:
- **Disaster recovery** (cluster links, replicators, project replica mode): a later
  stage. It needs a second cluster.
- **Identities, OIDC and bearer tokens** (AC-1): waiting for a separate discussion. The
  app talks to LXD as one admin client; whether it should act with each user's or
  project's own credentials instead is a design question to settle first.
- Forced delete, per-project images and backups volumes, GPU via CDI: small; pick up
  in Phase 10 or when needed.

**5c MicroCloud itself (evaluate, then decide).** MicroCloud 3.4 has a generally available
Cluster Manager with its own API, and an observability charm for COS/Grafana. Decide
whether the app should show MicroCloud-level facts (component versions, Cluster Manager
status) or leave them to those tools.

*Exit:* every page and action works against the upgraded 3.4 test cloud (`LIVE-TESTS.md`
updated and run), the recorded-fixture suites pass for both LTS releases, the 5b items
agreed with the owner are built (✅ 0.1.4), and README, DEPLOY.md and CEPH-METRICS.md say
which MicroCloud releases are supported (✅ 2026-11-12, DEPLOY.md "Supported releases").

*Decided 2026-11-12:* keep supporting MicroCloud 2.x; the 5b scope above; 5.21 fixtures
recorded. The owner moves the members to Ubuntu 26.04 (`do-release-upgrade`, one member at a
time: evacuate, `ceph osd set noout`, upgrade, reboot, check, unset, restore) **before**
the MicroCloud upgrade. In between, MicroCloud 2.1 / LXD 5.21 runs on 26.04's 7.0 kernel,
a combination LXD's support table doesn't list, so keep that gap short. node_exporter
then comes from 26.04; the `recorded-5.21` node samples are from 24.04.

*Ceph perf counters on Tentacle:* MicroCeph doesn't run `ceph-exporter` (checked
2026-11-12: its Tentacle snap on `main` has no exporter service or package, nothing in its
code sets `exclude_perf_counters`, and the PR adding the exporter, microceph#552, is
unmerged). Canonical's charm-microceph sets `exclude_perf_counters` to `False` instead.
So the route is the same here: after the upgrade, check `:9283/metrics` serves (a Tentacle
build once didn't start the module, microceph#874, fixed by #896 on 2026-09-24) and
`ceph config get mgr mgr/prometheus/exclude_perf_counters`; if the OSD latency metrics are
missing, `ceph config set mgr mgr/prometheus/exclude_perf_counters false` (owner's call,
as D23) and document it in `docs/CEPH-METRICS.md`. The Ceph page hides the latency views
with that hint when the counters are missing.
**Closed 2026-11-13:** on the test cloud the D23 setting (`false`, set on Squid) survived
the upgrade and the mgr restarts, so Tentacle 20.2.1 exports the OSD used/size and per-op
latency series with no change (`recorded-6.10-cluster/ceph_mgr.txt`). Nothing to do for
Tentacle; the longer-term risk is a watch item in Phase 8.

**Phase 6: Users and project access.** *Agreed 2026-11-13 (D81): the owner approved the plan
and the five defaults below. It leads to release 0.2.0; `main` is 0.2.0.dev0 meanwhile.* App users who see and change only the LXD projects they're granted,
while the app keeps one admin connection to LXD. OIDC, groups and LXD-side identities
stay later (Phase 10, AU-4/AC-1/AC-2).

*Verified first (2026-11-13, in `mcui-test`, admin certificate):*
- **LXD enforces a project's `restricted=true` against our admin certificate**, at
  creation and at edit. Refused were: a privileged container; a disk from a host path; a
  `unix-char` device; `raw.lxc`; nesting; adding a host-path disk to an existing instance;
  and making a profile in use privileged. A plain container was created as the control.
- So inside a restricted project, LXD itself closes the routes to the host, whatever the
  app sends. The app only has to guard the project's own settings, which hold
  `restricted`.

*Model:*
- **Global roles,** as today: **cluster admin** (everything) and **cluster viewer** (read
  everything). Existing `admin` and `viewer` users become these, so nothing changes for
  them.
- **Project grants:** a user without a global role gets grants of (project, role):

  | Project role | Can |
  |---|---|
  | **viewer** | see the project's instances, volumes, networks, profiles, images and placement groups; their graphs; its operations and audit entries |
  | **operator** | viewer, plus start, stop, restart, freeze; consoles and exec; files; instance and volume snapshots |
  | **admin** | operator, plus create, edit, rename, move within the project, copy and delete instances; the project's own profiles, volumes, images, networks, ACLs, forwards, load balancers and placement groups, within its limits |

- **Operator and admin grants only on `restricted=true` projects.** The grant form
  refuses others and explains why. Each request re-checks the project is still restricted,
  in case someone unset it with `lxc`, and turns a project grant into read-only meanwhile.
  Viewer grants work on any project.

*Defaults for the five open questions (owner to mark up):*
1. **The cluster view for project users:**
   - Hidden: Ceph, OVN, Warnings, the member pages, cluster-wide Operations, Storage
     pools, and the Projects admin.
   - The Overview shows only their instances, plus total cluster capacity (CPU, memory,
     disk), but no other project's names or figures. The Hosts table is hidden.
2. **Shared resources:** a project without its own networks, profiles or images uses
   `default`'s. Project users can see those by name and pick them in the create wizard,
   as far as the project's `restricted.networks.access` and similar keys allow, but can't
   edit them. They also see storage pools by name and free space, to choose a pool, but
   not the pool pages.
3. **Users are managed by cluster admins only.** A project admin can see who has access
   to the project, but not change it. Delegation can come later.
4. **No leaks of other projects' names:**
   - lists, operations, the audit log, metrics (`series`, `query`, `top`, `live`,
     `network-traffic`) and the live event stream are filtered on the server by the
     project label
   - a request for anything outside the user's projects answers 404, not 403, so it
     doesn't reveal what exists
5. **Phasing** (6a–6e below): the model and enforcement first, with a test over every
   route, then the screens.

*Enforcement design:*
- One authorization check in the backend: `authorize(session, project, action)`, with
  actions `read`, `operate` and `manage`, plus `cluster-read` and `cluster-admin`.
- Every route declares its policy. **A test lists all 135 routes and fails if any has no
  policy declared**, so a new route can't ship unchecked. The default is deny.
- **Project-scoped routes** take the project from the path (`/api/instances/{project}/…`)
  or the body, and check it.
- **List routes** filter on the server, never in the browser. Today the project picker
  filters in the browser (D44); that stays as a view, but the server decides what's
  returned.
- **Websockets:** `/api/events` drops events for other projects. Consoles and exec
  (`/api/streams`) check `operate` when the stream is started.

*Route classification* (135 routes in all, grouped by area; the exact policy is declared on
each route in the code):

| Area | Policy |
|---|---|
| Instances, files, consoles/exec, logs, snapshots, publish | project: read / operate / manage per action |
| Volumes, profiles, images, placement groups, ACLs, forwards, load balancers | project: read / manage |
| Networks (`/networks/{project}/…`), leases, state | project: read / manage; shared `default` networks read-only |
| Lists: instances, volumes, profiles, images, networks, allocations, operations | filtered to the user's projects |
| Metrics: series, query, top, live, network traffic, names, status | filtered by project label; host and Ceph series are cluster-read |
| Cluster: members, resources, hosts, server, storage pools, Ceph, OVN, warnings, evacuate/restore, registries | cluster-read / cluster-admin; pool names and free space are visible for the create wizard |
| Projects | list: own projects; create, edit, rename, delete: cluster-admin |
| Users, audit, auth | users: cluster-admin; audit: filtered; auth: any session |

*Steps:*
- ✅ **6a, the model** (2026-11-13):
  - project users and grants in the store, with a migration for existing databases
  - the users API and `mcui user grant`/`revoke`; above viewer only on restricted
    projects
  - one authorization layer (`mcui/auth/policy.py` and the checks in `api/deps.py`): every
    one of the 138 routes declares a policy, enforced by `tests/test_policy.py`
  - enforcement tests for a project user: 404 outside their projects, 403 without the
    right, read-only once a project stops being restricted

  Project users stay locked out (HTTP and websockets) until 6b filters lists, metrics,
  operations, the audit log and the streams. Existing users are cluster roles, so nothing
  changed for them.
- ✅ **6b, filtering** (2026-11-13). For a project user:
  - **Single-project lists** (images, profiles, networks, volumes, ACLs, placement
    groups) answer 404 for another project.
  - **Cross-project lists** are filtered: instances, projects, network allocations,
    operations, and a shared network's DHCP leases. Another project's operation is
    "not found", checked before any wait.
  - **The host's own interfaces** (`networks?target=`) need a cluster role.
  - **Metrics:** `series` and network traffic are filtered, and `top` keeps their rows.
    `query` must match one of their projects, because a sum across series would mix
    projects. The live snapshot keeps their instances and no member rows.
  - **The audit log:** only changes to their projects' objects, never user or login
    entries. Cluster viewers read all of it (owner's call, 2026-11-13; before Phase 6
    only admins could).
  - **The event stream:** only their projects' events.
  - **Console streams:** only their owner attaches. Starting one needs operate, so
    attaching no longer needs the admin role.

  Tested in `tests/test_policy.py` with a project user against data from other projects.
  A deliberately broken filter fails those tests. **Project users can now sign in.**

  Note on shared resources: LXD answers queries on a project without its own networks,
  profiles or images with `default`'s, so the agreed sharing needs no special case. A
  project without its own storage volumes would likewise show `default`'s custom volumes,
  so projects for users should keep `features.storage.volumes=true`, LXD's default.
- ✅ **6c, the UI** (2026-11-13):
  - **`frontend/src/access.ts`** mirrors the server's rules. Every former
    `role === "admin"` check is now the right question: operate or manage in the object's
    project, or cluster admin.
  - **A Users page** (cluster admins) to create, edit and delete users and to grant
    projects. It warns before an operator or admin grant on an unrestricted project.
  - **Navigation:** Storage, Ceph and Warnings only for global roles; Users only for
    cluster admins.
  - **The project picker** lists only the user's projects (the server filters it), and
    pages needing one project fall back to the user's first (`useCurrentProject`).
  - **An instance's Files and Console tabs** need operate. Its rename, copy, move,
    publish and delete need manage. The Overview shows no hosts or members to project
    users.
  - **The create wizards** offer only projects where the user is admin.

  Tested with fixtures (a project user's navigation, instance page and Overview; the Users
  page; the access rules), and the Users page was checked visually from the demo cluster.
- ✅ **6d, live checks** (2026-11-13, on the test cloud, owner's go-ahead):
  - **Setup:** `mcui-test` made restricted for the run, with a scratch app and its own
    user store: a project admin, an operator and a viewer of `mcui-test`.
  - **The "other project":** the owner's `default`, which has real instances. It was only
    read, or sent writes that had to be refused.
  - **44 API checks passed:**
    - the admin creates while LXD refuses a privileged container and a host-path disk
    - the operator starts and stops but can't delete or create
    - the viewer reads only
    - `default`'s instance is "not found" to all three, for reads and writes
    - lists, operations, allocations, metrics, audit and the cluster pages are as
      designed
  - **The event stream:** the operator got only `mcui-test` events, all of their own
    work; the admin also saw LXD's internal "Synchronizing operations", which belong to
    no project.
  - **The UI in Chrome as each user:** navigation, picker, instance tabs and actions as
    designed, with no refused request on any page.
  - **Found and fixed:**
    - LXD's operations and network allocations have **no `project` field**. The project
      comes from the object URL: `metadata.entity_url` on 6.x, `resources` on 5.21,
      `used_by` for allocations. See `mcui/lxd/entities.py`. The fixtures had assumed the
      field, so the tests now use the real shapes.
    - Member names are no longer links for project users.
    - The Overview no longer shows them LXD's endpoint.
  - **Cleanup:** the test container deleted and `mcui-test` restored exactly.

  Steps to repeat: `docs/LIVE-TESTS.md` P1–P6.
- ✅ **6e, docs** (2026-11-13):
  - **`docs/USERS.md`:** roles and project roles, preparing a restricted project (and
    keeping its own storage volumes), what project users see, managing users from the
    UI, CLI or API, how it's enforced, and upgrading to 0.2.0.
  - **The README:** who may do what, the user commands, and the Users screenshot.
  - **Requirements:** AU-2 extended and a new AU-5.
  - **Elsewhere:** `CLAUDE.md`'s doc table and `docs/CONFIGURATION.md`.
  - **One UI addition:** a **Restricted** checkbox when creating a project, and a
    Restricted column in the projects list.

  Phase 6 is done; next is release 0.2.0.

*Out of scope for Phase 6:*
- OIDC and other identity providers, and groups (AU-4).
- LXD-side identities per project (AC-2): option B in the discussion, a possible
  hardening step later behind the same grants.
- Delegated user management, and per-user API tokens.

**Phase 7: Backups, project moves and ISO upload** (pulled forward from the parity list,
owner 2026-11-13, D83). The parity items the owner wants first: IN-13, ST-4 (volume
backups), IN-6 (moving instances between projects) and IM-3, plus the part of PJ-2 they
turned out to need.

*Verified first (2026-11-13, in `mcui-test`, LXD 6.10.1):*
- **A restricted project refuses snapshots and backups by default.** LXD answered
  "does not allow for snapshot creation" (and "backup creation") until
  `restricted.snapshots=allow` and `restricted.backups=allow` were set. So in a default
  restricted project, Phase 6's operator snapshot right does nothing; USERS.md didn't say so.
- A custom volume's backup exports as a tarball and imports as a new volume under another
  name (`X-LXD-name`), snapshots included.
- An ISO uploads as a custom volume (`X-LXD-type: iso`, `content_type: iso`).
- Not checked: moving an instance between projects (needs a second project, so the owner
  creates `mcui-test-2` or runs it; D41), and whether LXD applies the target project's
  restrictions to a move and to a backup import. 7c checks both, with a crafted
  privileged backup.

*Rights (D81's levels):*

| Action | Needs |
|---|---|
| List backups | read |
| Create, rename, delete, download a backup (instance or volume) | operate (a download holds the files, as the Files tab does) |
| Import a backup as a new instance or volume | manage in the target project |
| Upload an ISO; attach it to a VM | manage |
| Move an instance to another project | manage in both projects |
| Edit a project's restriction keys | cluster admin (unchanged: the project's own settings) |

*Steps:*
- ✅ 2026-11-13 **7a, instance backups (IN-13):** a Backups tab on the instance page: list (name,
  created, expiry, instance only, optimized), create (name, expiry, instance only,
  optimized storage, compression), rename, delete, download (streamed through the backend).
  Import from Instances → Import backup: a tarball upload (streamed) with project, name and
  pool. When the project is restricted without `restricted.backups=allow`, say so instead of
  offering Create.
- ✅ 2026-11-13 **7b, volume backups (ST-4):** the same on the volume page; import as a new volume.
- ✅ 2026-11-13 **7c, moving instances between projects (IN-6):** the Move dialog gets a target project
  (with LXD's `instance_project_move`), offered only where the user can manage. Live: the
  owner's second project, plus the restriction checks above.
- ✅ 2026-11-13 **7d, ISO upload (IM-3):** Storage → Volumes → Upload ISO (project, pool, name, file;
  streamed, with progress); ISO volumes marked in the list. Attach one to a VM as a CD
  (`disk` device with `source` the volume, `boot.priority`) from the VM's configuration and
  from the create wizard.
- ✅ 2026-11-13 **7e, restrictions (PJ-2, the part needed now):** the project page edits the
  `restricted.*` keys as a form, snapshots and backups first, so a cluster admin can allow
  them. USERS.md corrected: operator snapshots and backups need them allowed.
- ✅ 2026-11-13 **7f:** README, USERS.md, LIVE-TESTS.md, screenshots; then release 0.3.0.

*Found in 7a (live, LXD 6.10.1):* importing a backup into any project with `limits.*` or
`restricted` is refused: LXD counts the backup's own `volatile.*` keys as low-level options.
`restricted.containers.lowlevel=allow` lets it through, but on a restricted project that
also opens `raw.lxc`. A crafted privileged backup was still refused. 7c found the way round
(live): import into an unrestricted project without limits, then move the instance into the
restricted one; a privileged instance is refused on the move.

*Watch:* backups live on the member that holds the instance, under its
`storage.backups_volume` (unset on the test cloud, so the member's root disk). Large backups
and ISOs stream through microcloud-ui without being held in memory, but the export and import
both need room on that member. DEPLOY.md says so.

**Phase 8: Metric store evaluation** (was Phase 7, before that 6 and 5; MO-14): benchmark SQLite against
Prometheus, VictoriaMetrics and others (e.g. TimescaleDB, InfluxDB, ClickHouse)
on ingest, query latency, disk use and ops burden; implement the chosen
adapter. Alerts (MO-13), export (MO-15), project quota
history (MO-10).

*✅ Done 2026-11-13: React Compiler lint rules.* `eslint-plugin-react-hooks` 7's recommended
rules are on. They flagged 12 places, now rewritten:
- The edit pages and the YAML editor take loaded values into their form during render
  (React's "adjusting state when a prop changes"), not in an effect.
- `events.ts` and the terminal use `useEffectEvent` for their latest callbacks.
- The consoles reset their status in the Reconnect handler, and the graphical console is
  keyed on the instance.

The original note:
- **`set-state-in-effect` (9):** seven edit pages (profile, project, ACL, network, pool,
  volume) copy server data into their form state in an effect, and so do the YAML editor
  and the console views. That pattern also caused a timing-dependent test. The fix is to
  key the form on the loaded object, or derive state during render.
- **`refs` (3):** `events.ts` and the terminal view read or write a ref during render.

Turn the rules on once those are rewritten. **TypeScript 7** waits for `typescript-eslint`
to support it (only below 6.1 in November 2026).

*Watch item: Ceph daemon perf counters.* Upstream Ceph has moved OSD perf counters to the
separate `ceph-exporter` daemon; the mgr's `exclude_perf_counters=false` is the
compatibility route and may be removed in a release after Tentacle. MicroCeph runs no
`ceph-exporter` today (microceph#552 unmerged). If both stay true when a new MicroCeph
track lands, OSD used/size and per-op latency (MO-7a) need another source: scrape
`ceph-exporter` if MicroCeph ships it, or read `ceph osd df` / `ceph osd perf` through the
mgr's REST API (needs a Ceph key). Check each new MicroCeph track's release notes and the
recorded mgr scrape; nothing to build until then.

**Phase 10a: Network peers, ACL logs, volume copy/move** (owner, 2026-11-13, D84). The parts
of the remaining parity list testable on the test cloud:
- **Peers (NW-7):** an OVN network's page lists its peers (status Pending until the other
  side peers back, then Created) and adds or deletes them, with an option to create the
  matching peer on the other network too. Manage in the project (and in the other one for
  the matching peer). Live: two temporary OVN networks `mcui-test-*` on the uplink (owner's
  permission), deleted afterwards.
- **ACL log viewer (NW-4):** an ACL's page shows LXD's log of its `logged` rules
  (`/1.0/network-acls/{name}/log`). Read right in the project.
- **Volume copy and move (ST-4):** copy a custom volume to another pool, member or project,
  and move it (rename is there already). Manage in both projects for a project change.
- Not here: SR-IOV (no hardware), zones (needs an external DNS server to check), cluster
  groups and config (cluster-wide: fixtures plus the owner's checklist), sign-in options.

**Phase 10: Remaining parity** (was Phase 8, before that 7 and 6; after packaging, which is
done). Backups, project moves and ISO upload moved to Phase 7 (D83). The rest of PJ-2
(restriction keys beyond 7e), sriov, peers, zones, ACL logs
(NW-3/4/6/7), cluster groups and config (CL-4/5). Login options evaluation (AU-4), with
identities (AC-1) once the auth model is agreed. OVN load balancers moved to Phase 5b.

**Phase 9: Packaging** (was Phase 8, before that 7). Wheel + systemd unit, OCI image, docs for running
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
