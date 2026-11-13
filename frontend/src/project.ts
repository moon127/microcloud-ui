import { createContext, useContext } from "react";
import { visibleProjects } from "./access";
import { useSession } from "./session";

export interface ProjectState {
  /** Selected project, or "" for all projects. */
  project: string;
  setProject: (p: string) => void;
}

export const ProjectContext = createContext<ProjectState>({ project: "", setProject: () => {} });

export const useProject = () => useContext(ProjectContext);

/** The project pages that need one should use: the one selected, or for "All projects"
 * (or one the user can't see) "default" for cluster roles and a project user's first. */
export function useCurrentProject(): string {
  const { project } = useProject();
  const mine = visibleProjects(useSession().user);
  if (mine === null) return project || "default";
  return project && mine.includes(project) ? project : (mine[0] ?? "default");
}
