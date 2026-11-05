import { createContext, useContext } from "react";
import type { User } from "./api";

export interface Session {
  user: User;
  onExpired: () => void;
  onLogout: () => void;
}

export const SessionContext = createContext<Session | null>(null);

export function useSession(): Session {
  const s = useContext(SessionContext);
  if (!s) throw new Error("useSession outside SessionContext");
  return s;
}
