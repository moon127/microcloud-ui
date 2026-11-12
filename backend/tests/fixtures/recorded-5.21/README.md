# LXD 5.21 baseline (recorded 2026-11-12)

Recorded with `mcui record-fixtures` from the test cloud just before its upgrade from
MicroCloud 2.1 LTS to 3.4 LTS (Phase 5.0, `docs/PLAN.md`): three members on LXD 5.21
(5.21.8 on member 1, 5.21.9 on members 2 and 3), MicroCeph Squid with the mgr
`prometheus` module, perf counters and RBD stats on (D23), MicroOVN 24.03, node_exporter
on every member (D58).

It holds everything the app reads: the server, members and their state and hardware,
instances (state and snapshots included), profiles, images, projects, storage pools per
member with capacity and volumes, networks with each OVN network's forwards, load
balancers, peers and state and the uplink's leases and per-member state, network ACLs and
allocations, operations, warnings, `/1.0/metrics` per member, the active Ceph mgr's
`/metrics` (`ceph_mgr.txt`) and node_exporter per member (`node_*.txt`).

IPs, MACs, fingerprints, certificates and hardware serials are scrubbed. Host names and
descriptions are kept (the owner's call, as in `../recorded`).

After the upgrade, the same command into `recorded-6.10/` gives the set to compare
against (Phase 5a).
