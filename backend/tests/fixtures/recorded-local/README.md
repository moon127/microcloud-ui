# Local storage pools (recorded 2026-11-11)

Recorded with `mcui record-fixtures` from a throwaway LXD 5.21.3 (built from the
release tarball) running in a VM in the `mcui-test` sandbox: a one-member cluster
`tl1` with loop-backed **zfs** (`lz`), **btrfs** (`lb`), **lvm** (`ll`) and **dir**
(`local-dir`) pools, an Alpine container on each (`c-<pool>`), custom volumes `v1`
(zfs, attached to `c-lz` at `/v1`, snapshots `s0`, `s2`) and `vb` (lvm, block).
Each container wrote and read 16 MiB just before recording.

What these show (tests in `tests/test_recorded_local.py`):

- Per-container disk counters list every host block device: the container on lvm
  repeats its I/O on the stacked `dm-*` layers and the disk, so D40 (skip `dm-*`,
  partitions and loops) counts it once.
- ZFS attributes no I/O to containers (all zero); that's ZFS, not mcui.
- Member state lists all local pools, so pool capacity history (MO-6) covers them.
