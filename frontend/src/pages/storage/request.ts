import type { NewPool, PoolDriver } from "../../api";

export interface PoolForm {
  name: string;
  driver: PoolDriver;
  description: string;
  /** cluster-wide keys from the form */
  fields: Record<string, string>;
  /** member -> its own source and size */
  perMember: Record<string, { source: string; size: string }>;
  /** member keys that are usually the same everywhere (zfs.pool_name, lvm.vg_name, …) */
  shared: Record<string, string>;
  extra: Record<string, string>;
}

export interface Field {
  key: string;
  label: string;
  placeholder?: string;
}

export const LOCAL: ReadonlySet<PoolDriver> = new Set(["dir", "zfs", "btrfs", "lvm"]);

export const DRIVERS: { driver: PoolDriver; label: string; note: string }[] = [
  { driver: "zfs", label: "ZFS", note: "Local on each member: a disk, partition, existing pool, or a loop file" },
  { driver: "lvm", label: "LVM", note: "Local on each member: a disk or existing volume group, or a loop file" },
  { driver: "btrfs", label: "Btrfs", note: "Local on each member: a disk, partition, or a loop file" },
  { driver: "dir", label: "Directory", note: "Local on each member: a directory (no quotas or fast snapshots)" },
  { driver: "ceph", label: "Ceph RBD", note: "Shared by the cluster: block volumes in a Ceph pool" },
  { driver: "cephfs", label: "CephFS", note: "Shared by the cluster: custom filesystem volumes only" },
];

/** Cluster-wide fields per driver. */
export const FIELDS: Record<PoolDriver, Field[]> = {
  dir: [],
  btrfs: [{ key: "btrfs.mount_options", label: "Mount options", placeholder: "user_subvol_rm_allowed" }],
  zfs: [],
  lvm: [
    { key: "lvm.use_thinpool", label: "Use a thin pool (true/false)", placeholder: "true" },
    { key: "volume.block.filesystem", label: "Volume filesystem", placeholder: "ext4" },
  ],
  ceph: [
    { key: "ceph.osd.pool_name", label: "Ceph OSD pool", placeholder: "same as the pool name" },
    { key: "ceph.cluster_name", label: "Ceph cluster name", placeholder: "ceph" },
    { key: "ceph.user.name", label: "Ceph user", placeholder: "admin" },
    { key: "ceph.osd.pg_num", label: "Placement groups", placeholder: "32" },
  ],
  cephfs: [
    { key: "source", label: "CephFS filesystem (and optional path)", placeholder: "lxd_cephfs" },
    { key: "cephfs.cluster_name", label: "Ceph cluster name", placeholder: "ceph" },
    { key: "cephfs.user.name", label: "Ceph user", placeholder: "admin" },
    { key: "cephfs.create_missing", label: "Create the filesystem if missing (true/false)" },
  ],
};

/** Member-specific keys that are usually the same on every member. */
export const SHARED: Partial<Record<PoolDriver, Field[]>> = {
  zfs: [{ key: "zfs.pool_name", label: "ZFS pool name", placeholder: "same as the pool name" }],
  lvm: [
    { key: "lvm.vg_name", label: "Volume group", placeholder: "same as the pool name" },
    { key: "lvm.thinpool_name", label: "Thin pool name", placeholder: "LXDThinPool" },
  ],
};

export const emptyPool = (): PoolForm => ({
  name: "",
  driver: "zfs",
  description: "",
  fields: {},
  perMember: {},
  shared: {},
  extra: {},
});

export function buildPool(f: PoolForm, members: string[], features: Record<string, boolean> = {}): NewPool {
  const config: Record<string, string> = { ...f.extra };
  for (const { key } of FIELDS[f.driver]) {
    const v = (f.fields[key] ?? "").trim();
    if (v) config[key] = v;
  }
  // LXD 6.8+ refuses source on CephFS pools and takes cephfs.path (storage_remote_drop_source)
  if (f.driver === "cephfs" && features.cephfs_path && config.source) {
    config["cephfs.path"] = config.source;
    delete config.source;
  }
  const out: NewPool["members"] = {};
  if (LOCAL.has(f.driver)) {
    for (const m of members) {
      const cfg: Record<string, string> = {};
      const own = f.perMember[m] ?? { source: "", size: "" };
      if (own.source.trim()) cfg.source = own.source.trim();
      if (own.size.trim() && f.driver !== "dir") cfg.size = own.size.trim();
      for (const { key } of SHARED[f.driver] ?? []) {
        const v = (f.shared[key] ?? "").trim();
        if (v) cfg[key] = v;
      }
      if (Object.keys(cfg).length) out[m] = cfg;
    }
  }
  return { name: f.name.trim(), driver: f.driver, description: f.description.trim(), config, members: out };
}

export function poolProblems(f: PoolForm): string[] {
  const out: string[] = [];
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(f.name.trim())) out.push("Name: letters, digits, dots, dashes and underscores");
  if (f.driver === "cephfs" && !(f.fields.source ?? "").trim()) out.push("Enter the CephFS filesystem");
  return out;
}
