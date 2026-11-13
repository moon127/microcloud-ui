# LXD 6.10 on the test cloud (recorded 2026-11-13)

Recorded with `mcui record-fixtures` from the test cloud after its upgrade from
MicroCloud 2.1 LTS to 3.4 LTS (Phase 5, `docs/PLAN.md`): three members on Ubuntu 26.04
with **LXD 6.10.1**, **MicroCeph 20.2.1 Tentacle**, **MicroOVN 26.03** and MicroCloud
3.4.1, node_exporter on every member (D58). The same cluster as `../recorded-5.21`, so
the two sets compare directly; `../recorded-6.10` is the one-member VM with the new
objects (placement groups, registries, load-balancer pools, multi-volume snapshot).

Beyond the 5.21 set's files: image registries (the built-ins), placement groups (none),
each OVN network's load-balancer pools (none) and the `macvlan0` network's leases and
per-member state.

The mgr settings from D23 survived the Squid → Tentacle upgrade: `ceph_mgr.txt` has the
OSD used/size and per-op latency series without any change after it.

IPs, MACs, fingerprints, certificates and hardware serials are scrubbed. Host names and
descriptions are kept (the owner's call, as in `../recorded`). Recorded the morning after
the owner cleared MicroOVN 26.03's stuck `flow-restore-wait` (`docs/DEPLOY.md`), so OVN
network state shows an active gateway chassis.
