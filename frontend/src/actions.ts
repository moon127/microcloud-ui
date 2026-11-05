import { createContext, useContext } from "react";
import type { Started } from "./api";

export interface Actions {
  /** Run a change, follow its LXD operation to the end, report it as a toast. */
  run: (label: string, start: () => Promise<Started | void>) => Promise<boolean>;
  /** Bumped after every finished change, so views can reload. */
  version: number;
}

export const ActionsContext = createContext<Actions | null>(null);

export function useActions(): Actions {
  const a = useContext(ActionsContext);
  if (!a) throw new Error("useActions outside ActionsProvider");
  return a;
}
