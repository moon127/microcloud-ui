import { useContext, useEffect, useRef, useState } from "react";
import { ApiError } from "./api";
import { SessionContext } from "./session";

export interface ApiState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
}

/**
 * Load data whenever ``key`` changes, optionally refreshing every ``refreshMs``.
 * A 401 ends the session (the caller's SessionContext handles it).
 */
export function useApi<T>(key: string, load: () => Promise<T>, refreshMs?: number): ApiState<T> {
  const [state, setState] = useState<ApiState<T>>({ data: null, error: null, loading: true });
  const session = useContext(SessionContext);
  const loadRef = useRef(load);
  loadRef.current = load;
  const expiredRef = useRef(session?.onExpired);
  expiredRef.current = session?.onExpired;

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setState((s) => ({ ...s, loading: true }));

    async function run() {
      try {
        const data = await loadRef.current();
        if (active) setState({ data, error: null, loading: false });
      } catch (e) {
        if (!active) return;
        if (e instanceof ApiError && e.status === 401) {
          expiredRef.current?.();
          return;
        }
        setState((s) => ({ ...s, error: e instanceof Error ? e.message : String(e), loading: false }));
      }
      if (active && refreshMs) timer = setTimeout(run, refreshMs);
    }
    void run();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [key, refreshMs]);

  return state;
}
