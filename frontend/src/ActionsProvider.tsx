import { useCallback, useMemo, useRef, useState, type ReactNode } from "react";
import { ActionsContext } from "./actions";
import { waitOperation, type Operation, type Started } from "./api";

export interface Toast {
  id: number;
  label: string;
  state: "running" | "done" | "failed";
  message?: string;
}

const ACTIVE = new Set(["Pending", "Running", "Ready", "Started", "Freezing", "Cancelling"]);

async function finish(op: Operation): Promise<Operation> {
  let cur = op;
  while (ACTIVE.has(cur.status)) cur = await waitOperation(cur.id);
  return cur;
}

export default function ActionsProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [version, setVersion] = useState(0);
  const next = useRef(1);

  const update = (id: number, patch: Partial<Toast>) =>
    setToasts((ts) => ts.map((t) => (t.id === id ? { ...t, ...patch } : t)));

  const run = useCallback(async (label: string, start: () => Promise<Started | void>) => {
    const id = next.current++;
    setToasts((ts) => [...ts, { id, label, state: "running" }]);
    try {
      const st = await start();
      const op = st?.operation ? await finish(st.operation) : null;
      if (op && op.status !== "Success") throw new Error(op.err || op.status);
      update(id, { state: "done" });
      return true;
    } catch (e) {
      update(id, { state: "failed", message: e instanceof Error ? e.message : String(e) });
      return false;
    } finally {
      setVersion((v) => v + 1);
    }
  }, []);

  const value = useMemo(() => ({ run, version }), [run, version]);
  return (
    <ActionsContext.Provider value={value}>
      {children}
      <ul className="toasts" aria-live="polite" aria-label="Actions">
        {toasts.map((t) => (
          <li key={t.id} className={`toast ${t.state}`}>
            <span>
              {t.state === "running" ? "⏳" : t.state === "done" ? "✓" : "✗"} {t.label}
              {t.message ? `: ${t.message}` : ""}
            </span>
            {t.state !== "running" && (
              <button type="button" aria-label={`Dismiss ${t.label}`} onClick={() => setToasts((ts) => ts.filter((x) => x.id !== t.id))}>
                ×
              </button>
            )}
          </li>
        ))}
      </ul>
    </ActionsContext.Provider>
  );
}

