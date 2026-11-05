import { createContext, useContext } from "react";

export interface ProjectState {
  /** Selected project, or "" for all projects. */
  project: string;
  setProject: (p: string) => void;
}

export const ProjectContext = createContext<ProjectState>({ project: "", setProject: () => {} });

export const useProject = () => useContext(ProjectContext);
