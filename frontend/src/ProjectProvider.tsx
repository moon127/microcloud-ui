import { useMemo, useState, type ReactNode } from "react";
import { ProjectContext } from "./project";

const KEY = "mcui.project";

function stored(): string {
  try {
    return localStorage.getItem(KEY) ?? "";
  } catch {
    return "";
  }
}

export function ProjectProvider({ children }: { children: ReactNode }) {
  const [project, set] = useState(stored);
  const value = useMemo(
    () => ({
      project,
      setProject: (p: string) => {
        set(p);
        try {
          localStorage.setItem(KEY, p);
        } catch {
          // private mode: keep it for this tab only
        }
      },
    }),
    [project],
  );
  return <ProjectContext.Provider value={value}>{children}</ProjectContext.Provider>;
}
