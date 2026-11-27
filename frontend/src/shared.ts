// Objects a project shares with the default project (D89). Without its own
// features.<kind>, LXD applies a project's writes to default's objects, so the server
// refuses them for project users; the UI doesn't offer them.
import { fetchProject } from "./api";
import { useAccess, type Action } from "./access";
import { useApi } from "./useApi";

export type SharedKind = "profiles" | "images" | "storage.volumes" | "networks";

/** May the user do ``action`` on ``kind`` in ``project``? As ``access.can``, and for a
 * project user also the project's own ``features.<kind>`` (false until it's known). */
export function useCanWrite(project: string, action: Action, kind: SharedKind): { can: boolean; shared: boolean } {
  const access = useAccess();
  const projectUser = access.user.role === "project";
  const cfg = useApi(`project-features:${project}:${projectUser}`, () =>
    projectUser ? fetchProject(project).then((p) => p.config) : Promise.resolve(null),
  );
  const allowed = access.can(project, action);
  if (!projectUser) return { can: allowed, shared: false };
  const own = cfg.data?.[`features.${kind}`] === "true";
  // shared only when the config is known: an error or loading says nothing (D90)
  return { can: allowed && own, shared: allowed && cfg.data != null && !own };
}
