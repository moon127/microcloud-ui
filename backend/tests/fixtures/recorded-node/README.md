Two node_exporter 1.7.0 scrapes (Ubuntu 24.04 package, plain http) of one test-cloud
host, 15 s apart, recorded 2026-11-11. Only the metric families mcui keeps
(`mcui.metrics.select.NODE_*`, `node_cpu_seconds_total`) and `node_exporter_build_info`
are included; they hold no addresses, MACs or host names. The host has two disks
(sda, sdb), LVM (dm-*), mapped Ceph RBD images with partitions (rbdN, rbdNpM) and the
zfs module loaded without a pool.
