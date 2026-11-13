import { createContext, useContext } from "react";
import type { Operation, Started } from "./api";

export interface RunOptions {
  /** Called with the operation as it progresses (polled every couple of seconds). */
  onProgress?: (op: Operation) => void;
  /** Called with the failure message. */
  onError?: (message: string) => void;
}

/** One of several changes made together (a bulk action), named for the summary. */
export interface Item {
  name: string;
  start: () => Promise<Started | void>;
}

export interface Actions {
  /** Run a change, follow its LXD operation to the end, report it as a toast. */
  run: (label: string, start: () => Promise<Started | void>, opts?: RunOptions) => Promise<boolean>;
  /** Run several changes at once under one toast that counts them off and, at the end,
   * names any that failed. True if all succeeded. */
  runAll: (label: string, items: Item[]) => Promise<boolean>;
  /** Bumped after every finished change, so views can reload. */
  version: number;
}

export const ActionsContext = createContext<Actions | null>(null);

export function useActions(): Actions {
  const a = useContext(ActionsContext);
  if (!a) throw new Error("useActions outside ActionsProvider");
  return a;
}
