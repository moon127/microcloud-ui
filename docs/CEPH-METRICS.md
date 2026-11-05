# MicroCeph metrics setup for microcloud-ui

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

## 2. Recommended settings (DEFERRED on the test cloud)

> **Status (2026-11-05):** only the module itself is enabled on the test cloud.
> The two settings below add mgr load, so they are deferred until after Phase 1
> and revisited then (see PLAN.md). microcloud-ui must **support but not require**
> them: detect whether `ceph_osd_apply_latency_ms` / `ceph_rbd_*` series exist and
> show "not enabled, see docs/CEPH-METRICS.md" instead of empty graphs.


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

# review
sudo microceph.ceph config dump | grep mgr/prometheus
```

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

## 4. Firewall
Allow TCP 9283 to every mgr host from wherever microcloud-ui runs. Scrape all
mgr hosts: the mgr can fail over, and the active one is whichever answers with data.

## Reverting
```bash
sudo microceph.ceph config rm mgr mgr/prometheus/exclude_perf_counters
sudo microceph.ceph config rm mgr mgr/prometheus/rbd_stats_pools
sudo microceph.ceph mgr module disable prometheus && sudo microceph.ceph mgr module enable prometheus
```
