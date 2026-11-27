// Who may do what, as the server decides it (D81, backend mcui/auth/policy.py). The
// server enforces it; the UI only stops offering what it would refuse.
import type { User } from "./api";
import { useSession } from "./session";

export type Action = "read" | "operate" | "manage";
const NEED: Record<Action, number> = { read: 0, operate: 1, manage: 2 };
const RANK = { viewer: 0, operator: 1, admin: 2 } as const;

/** May ``user`` do ``action`` in ``project``? */
export function can(user: User, project: string, action: Action): boolean {
  if (user.role === "admin") return true;
  if (user.role === "viewer") return action === "read";
  const role = user.grants?.[project];
  return role !== undefined && RANK[role] >= NEED[action];
}

/** The projects a project user may see; ``null`` for a global role (all of them). */
export const visibleProjects = (user: User): string[] | null =>
  user.role === "project" ? Object.keys(user.grants ?? {}).sort() : null;

/** The projects where ``user`` may manage (create in), for a project user. */
export const manageable = (user: User): string[] =>
  Object.entries(user.grants ?? {})
    .filter(([, role]) => role === "admin")
    .map(([p]) => p)
    .sort();

/** Does a restricted project refuse ``what``? LXD's default for both, until set to allow
 * (seen live on LXD 6.10, D83). */
export const restrictionBlocks = (config: Record<string, string> | undefined, what: "backups" | "snapshots") =>
  config?.restricted === "true" && config[`restricted.${what}`] !== "allow";

/** LXD 6.10 refuses a backup's own ``volatile.*`` keys as low-level options in a project with
 * limits or restrictions, unless ``restricted.containers.lowlevel=allow`` (seen live, D83). */
export const importRefused = (config: Record<string, string> = {}) =>
  (config.restricted === "true" || Object.keys(config).some((k) => k.startsWith("limits."))) &&
  config["restricted.containers.lowlevel"] !== "allow";

// device types that reach the host's hardware; host-path disks too (backend policy.py)
const HOST_TYPES = new Set(["usb", "pci", "gpu", "unix-char", "unix-block", "unix-hotplug", "proxy", "infiniband"]);
const HOST_NICTYPES = new Set(["physical", "macvlan", "sriov", "ipvlan"]);

/** A device that uses the host directly (backend policy.is_host_device, widened in 0.3.4):
 * a hardware type, a proxy, a NIC on a host interface, or a disk that isn't a pool volume
 * (a host path, or a Ceph source). */
export const isHostDevice = (d: Record<string, string>) => {
  if (HOST_TYPES.has(d.type)) return true;
  if (d.type === "nic") return HOST_NICTYPES.has(d.nictype) || ("parent" in d && !("network" in d));
  if (d.type !== "disk" || d.pool) return false;
  return /^(\/|ceph:|cephfs:)/.test(d.source ?? "");
};

/** May ``user`` add host paths and USB devices in ``project``? Cluster admins, and project
 * admins whose grant carries the right (D86). */
export const mayUseHostDevices = (user: User, project: string) =>
  user.role === "admin" || (user.grants?.[project] === "admin" && (user.host_devices ?? []).includes(project));

export interface Access {
  user: User;
  /** a global role: cluster-wide pages (members, Ceph, OVN, warnings, storage pools) */
  cluster: boolean;
  /** cluster admin: users, projects, members, pools, cluster settings */
  clusterAdmin: boolean;
  can: (project: string, action: Action) => boolean;
  /** may manage in at least one project, or in ``project`` when given */
  canManageSomewhere: (project?: string) => boolean;
  /** host paths and USB devices (D86) */
  hostDevices: (project: string) => boolean;
}

export function access(user: User): Access {
  return {
    user,
    cluster: user.role !== "project",
    clusterAdmin: user.role === "admin",
    can: (project, action) => can(user, project, action),
    canManageSomewhere: (project) =>
      project
        ? can(user, project, "manage")
        : user.role === "admin" || Object.values(user.grants ?? {}).includes("admin"),
    hostDevices: (project) => mayUseHostDevices(user, project),
  };
}

export const useAccess = (): Access => access(useSession().user);
