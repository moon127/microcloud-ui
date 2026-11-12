# LXD 6.10 / MicroCloud 3.4 components (recorded 2026-11-12)

Recorded with `mcui record-fixtures` from a throwaway VM in the `mcui-test` sandbox
(Phase 5, `docs/PLAN.md`): Ubuntu 26.04.1 with the MicroCloud 3.4 component snaps,
**LXD 6.10.1** (`6/stable`) as a one-member cluster, **MicroCeph 20.2.1 Tentacle** with
three loop-file OSDs, the mgr `prometheus` module and RBD stats, **MicroOVN 26.03**
(OVN NB 7.18), and node_exporter 1.10.2 from Ubuntu 26.04. Set up by hand as MicroCloud
does: the LXD snap connected to MicroCeph and MicroOVN, a Ceph RBD pool `remote`, a CephFS
pool `remote-fs` (created with `cephfs.path`, LXD 6.8+), a bridge `lxdbr0` as the OVN
uplink and an OVN network `default`.

Contents beyond the 5.21 set: three Alpine containers; a placement group `pg-spread` used
by two of them; an image registry `lab-mirror` beside the built-in ones; a custom volume
`v1` attached to `c1` and a multi-volume snapshot `c1/s-multi` (with `v1/snap0`); an OVN
load-balancer pool `web` (c1, c2; health checks every 5 s) behind a load balancer whose
listen address was allocated automatically.

Two files were captured by hand, scrubbed the same way:
- `ceph_mgr_tentacle_defaults.txt`: the mgr with Tentacle's default
  `exclude_perf_counters=true` (`ceph_mgr.txt` is with it `false` after a mgr restart).
- `1.0_operations_recursion_2_bulk_stop.json`: a bulk stop through `PUT /1.0/instances`,
  a parent operation with `child_count` 3 and its children (operations finish too fast
  for `record-fixtures`).

Seen while recording: every operation carries `entity_url` and an empty `resources`;
creating a load balancer failed (`invalid CIDR address: none/32`) while the uplink
bridge had `ipv6.address: none`, and worked once it had an IPv6 subnet.
