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

export interface Access {
  user: User;
  /** a global role: cluster-wide pages (members, Ceph, OVN, warnings, storage pools) */
  cluster: boolean;
  /** cluster admin: users, projects, members, pools, cluster settings */
  clusterAdmin: boolean;
  can: (project: string, action: Action) => boolean;
  /** may manage in at least one project, or in ``project`` when given */
  canManageSomewhere: (project?: string) => boolean;
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
  };
}

export const useAccess = (): Access => access(useSession().user);
