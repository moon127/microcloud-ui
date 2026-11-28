# MicroCeph metrics setup for microcloud-ui

microcloud-ui shows these on its **Ceph** page (`/ceph`): health, monitors, OSDs, pools,
latency history and the busiest instance disks/volumes (from the per-RBD-image stats below).

**Monitors** lists each monitor with its host, rank, address and whether it's in quorum,
from the mgr. Its free disk space comes from node_exporter on its host (see
[`HOST-METRICS.md`](HOST-METRICS.md)): the filesystem holding
`/var/snap/microceph/common/data/mon/ceph-<name>`, counted as Ceph counts it (space
available over size, in whole percent). By default Ceph raises `MON_DISK_LOW` at 30% free
or less and `MON_DISK_CRIT` at 5% or less (`mon_data_avail_warn`, `mon_data_avail_crit`);
the page flags the same, as the mgr doesn't export the cluster's own values. A monitor's
host is matched to an LXD member by the host's own name as node_exporter reports it
(`uname -n`), else by the member's name, ignoring case and, failing that, the domain. In a bigger cluster a
monitor can run on a host that isn't an LXD member, or has no node_exporter: it shows
"not monitored". The mgr's health metric names only the check, so this is where to see
*which* monitor is low.

### Balance and capacity

The OSD table helps answer "why is this OSD nearly full when the cluster isn't?":

- **VAR** is an OSD's % used over the cluster's average, as `ceph osd df` shows it;
  1.2 or more is in bold. OSDs that are out are left out of the average and the hosts. Hover it for the OSD's placement groups against an even spread
  by size across its host's OSDs.
- **Reweight** appears only when an OSD that's in has a reweight other than 1.
- **By host**, under the table: each host's OSDs added up. With as many hosts as the
  pool holding the most data has copies
  (three hosts, three copies: MicroCeph puts copies on different hosts once it has three),
  every host holds a full copy of the data. The fullest host sets the limit, and the
  extra space on a bigger host can't be used; the page says so. The pools' **Available**
  is Ceph's own estimate of what more fits, and the `ceph_pool` alert
  ([`ALERTS.md`](ALERTS.md)) watches it. Raw capacity can look comfortable meanwhile.

The mgr doesn't export CRUSH weights, so they aren't shown (`ceph osd df` has them).
microcloud-ui only reads Ceph; it doesn't change weights or balancer settings.

**The balancer.** Ceph's balancer module evens out placement groups between OSDs,
moving a few at a time. On our MicroCloud test cloud it was on, in `upmap` mode;
check yours:

```bash
sudo ceph balancer status      # want: "active": true, "mode": "upmap"
sudo ceph osd df               # %USE, VAR and PGS per OSD
```

It balances placement-group *counts*, not bytes, and is content once every OSD is within
`upmap_max_deviation` placement groups of its share in each pool (default 5; the page's
hover counts all pools together). On a small OSD, five
extra placement groups is a lot: on the test cloud a 112 GiB OSD sharing a host with a
250 GiB one had 65 placement groups against a share of 60, about 7 GiB more, and sat at
84% (VAR 1.26) while the balancer reported "no optimization needed". Tightening it lets
the balancer move those few:

```bash
sudo ceph config set mgr mgr/balancer/upmap_max_deviation 1
sudo ceph balancer eval        # lower is better; run before and after
# undo
sudo ceph config rm mgr mgr/balancer/upmap_max_deviation
```

Moves keep copies on different hosts; with as many hosts as copies they happen within
each host. Each one copies data while it runs, so do it while no OSD is near full. Prefer
this to `ceph osd reweight` or `reweight-by-utilization`, the older way of evening out
OSDs: with the upmap balancer in charge, reweights are best left at 1 so the two don't
pull in different directions. It can't help when a whole host is the limit (above); only
more disk in the smaller hosts can.

microcloud-ui reads Ceph metrics from the ceph-mgr `prometheus` module:
plain HTTP on port 9283 on each mgr host, served by the **active** mgr only,
with **no authentication or TLS**. Reference: https://docs.ceph.com/en/squid/mgr/prometheus/

Run all commands on any MicroCeph member.

## 1. Check it's enabled
```bash
sudo microceph.ceph mgr services          # want: "prometheus": "http://<host>:9283/"
sudo microceph.ceph mgr module ls -f json | python3 -c 'import json,sys;print("prometheus" in json.load(sys.stdin)["enabled_modules"])'
```
If it isn't enabled:
```bash
sudo microceph.ceph mgr module enable prometheus
```

## 2. Recommended settings (enabled on the test cloud since 2026-11-05)

> **Status (2026-11-05):** both settings are enabled on the test cloud and kept
> on after the MO-7a checkpoint (D23). The UI views that use them are **not built yet**: they
> come in Phase 4 and will be built against the fixture recorded below.
> microcloud-ui must **support but not require** the settings: detect whether
> `ceph_osd_apply_latency_ms` / `ceph_rbd_*` series exist and show "not enabled,
> see docs/CEPH-METRICS.md" instead of empty graphs.
>
> **Observed with both on (Ceph 19.2.3 squid, 3 mgr hosts):** the active mgr
> (node-1) returns 267 KiB, 2,784 samples in 656 families, in
> ~35 ms; the standbys return HTTP 200 with an empty body. OSD latency is present
> (`ceph_osd_apply/commit_latency_ms`, `ceph_osd_op_r/w_latency_sum/count`, 6
> OSDs) and per-image RBD counters cover 34 images in `lxd_remote` (labels
> `pool`, `namespace`, `image`). LXD image names map to objects as
> `container_<name>`, `virtual-machine_<name>`, `image_<fingerprint>…` (cached
> images) and `lxd_lxd` (LXD's own volume). BlueStore counters are the biggest
> addition (858 samples). Scrubbed sample:
> `backend/tests/fixtures/recorded/ceph_mgr_active_perf_rbd_on.txt`.
> The owner chose to keep them on; §4 shows how to check the mgr cost on other clusters.


| Setting | Why |
|---|---|
| `exclude_perf_counters false` | Since Reef, daemon perf counters (OSD latency, op rates, BlueStore stats) are expected from separate `ceph-exporter` daemons. cephadm deploys those; **MicroCeph doesn't**. Without this you lose OSD latency and per-daemon performance. Safe on small clusters. |
| `rbd_stats_pools <lxd pool>` | Per-RBD-image IOPS, throughput and latency, i.e. per instance root disk and custom volume on Ceph, as seen by Ceph. Limit it to the LXD pool(s) rather than `*`. |
| `scrape_interval 15` | Default; keep it equal to microcloud-ui's interval. |
| `server_addr <mgmt IP>` (optional, per host) | Binds to the management network instead of all addresses (`::`), since the endpoint is unauthenticated. |

Find the Ceph pool(s) LXD uses (MicroCloud usually names them `lxd_remote` for RBD and `lxd_cephfs` for CephFS; check yours):
```bash
lxc storage list
lxc storage get remote ceph.osd.pool_name
sudo microceph.ceph osd pool ls
```

Apply:
```bash
sudo microceph.ceph config set mgr mgr/prometheus/exclude_perf_counters false
sudo microceph.ceph config set mgr mgr/prometheus/rbd_stats_pools "lxd_remote"   # your pool name(s), comma separated
sudo microceph.ceph config set mgr mgr/prometheus/rbd_stats_pools_refresh_interval 300
sudo microceph.ceph config set mgr mgr/prometheus/scrape_interval 15

# the module must be restarted to apply changes
sudo microceph.ceph mgr module disable prometheus
sudo microceph.ceph mgr module enable prometheus
# Ceph Tentacle (MicroCloud 3.4): that isn't enough for exclude_perf_counters;
# restart the mgr daemon itself (on the host running the active mgr)
sudo snap restart microceph.mgr

# review
sudo microceph.ceph config dump | grep mgr/prometheus
```

### Ceph Tentacle (MicroCloud 3.4 LTS)

Ceph Tentacle's mgr leaves the OSD performance counters out by default
(`exclude_perf_counters` is `true`); upstream now reads them with a separate
`ceph-exporter` daemon, which **MicroCeph doesn't run** (checked 2026-11-12). Measured on
MicroCeph Tentacle 20.2.1 in a test VM:

| Series | Tentacle default | `exclude_perf_counters false` + mgr restart |
|---|---|---|
| `ceph_osd_apply_latency_ms`, `_commit_latency_ms`, `ceph_osd_up`/`_in`, pools, `ceph_rbd_*` | yes | yes |
| `ceph_osd_stat_bytes`/`_used` (OSD used and size) | **no** | yes |
| `ceph_osd_op_r_latency_*`, `ceph_osd_op_w_latency_*` (per-op latency) | **no** | yes |

Without them, the Ceph page hides OSD used/size and the per-op latency graphs and shows
these two commands. The setting is kept in Ceph's configuration database, so a cluster
that had it on Squid (step 2) keeps it through the upgrade (seen on the test cloud,
2026-11-13: the series were there with no change); check with
`sudo microceph.ceph config get mgr mgr/prometheus/exclude_perf_counters`. Changing it
needs the mgr daemon restarted (`sudo snap restart microceph.mgr`): disabling and
re-enabling the module wasn't enough in the test.

## 3. Test from the machine running microcloud-ui
```bash
for ip in <member1-ip> <member2-ip> <member3-ip>; do
  echo "== $ip"; curl -s --max-time 5 "http://$ip:9283/metrics" | grep -cE '^ceph_'
done
# exactly one host (the active mgr) should report a large count

curl -s "http://<active-mgr-ip>:9283/metrics" | grep -E '^ceph_health_status|^ceph_osd_up|^ceph_osd_apply_latency_ms|^ceph_rbd_read_ops' | head
```
- `ceph_osd_apply_latency_ms` appearing shows `exclude_perf_counters false` worked.
- `ceph_rbd_*` series appear once the pool has images, after up to one refresh interval.

## 4. Measuring the mgr cost
The prometheus module collects on its own `scrape_interval`, so the cost is in
the mgr process, not in our requests. On the active mgr host, sample ceph-mgr
CPU for a minute with the settings on, then again after reverting:
```bash
top -b -d 15 -n 5 -p "$(pgrep -d, -f ceph-mgr)" | grep ceph-mgr
```

## 5. Firewall
Allow TCP 9283 to every mgr host from wherever microcloud-ui runs. Scrape all
mgr hosts: the mgr can fail over, and the active one is whichever answers with data.

## Reverting
```bash
sudo microceph.ceph config rm mgr mgr/prometheus/exclude_perf_counters
sudo microceph.ceph config rm mgr mgr/prometheus/rbd_stats_pools
sudo microceph.ceph mgr module disable prometheus && sudo microceph.ceph mgr module enable prometheus
```
