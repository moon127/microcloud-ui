import { useCallback, useMemo, useRef, useState, type ReactNode } from "react";
import { ActionsContext, type Item, type RunOptions } from "./actions";
import { waitOperation, type Operation, type Started } from "./api";

export interface Toast {
  id: number;
  label: string;
  state: "running" | "done" | "failed";
  message?: string;
}

const ACTIVE = new Set(["Pending", "Running", "Ready", "Started", "Freezing", "Cancelling"]);

/** Short waits when someone is showing progress, so it updates as it goes. */
const PROGRESS_WAIT = 2;

async function finish(op: Operation, onProgress?: (op: Operation) => void): Promise<Operation> {
  let cur = op;
  onProgress?.(cur);
  while (ACTIVE.has(cur.status)) {
    cur = await waitOperation(cur.id, onProgress ? PROGRESS_WAIT : undefined);
    onProgress?.(cur);
  }
  return cur;
}

export default function ActionsProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [version, setVersion] = useState(0);
  const next = useRef(1);

  const update = (id: number, patch: Partial<Toast>) =>
    setToasts((ts) => ts.map((t) => (t.id === id ? { ...t, ...patch } : t)));

  const run = useCallback(async (label: string, start: () => Promise<Started | void>, opts: RunOptions = {}) => {
    const id = next.current++;
    setToasts((ts) => [...ts, { id, label, state: "running" }]);
    try {
      const st = await start();
      const op = st?.operation ? await finish(st.operation, opts.onProgress) : null;
      if (op && op.status !== "Success") throw new Error(op.err || op.status);
      update(id, { state: "done" });
      return true;
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      update(id, { state: "failed", message });
      opts.onError?.(message);
      return false;
    } finally {
      setVersion((v) => v + 1);
    }
  }, []);

  const runAll = useCallback(async (label: string, items: Item[]) => {
    const id = next.current++;
    const total = items.length;
    let finished = 0;
    const failed: string[] = [];
    setToasts((ts) => [...ts, { id, label, state: "running", message: `0 of ${total} done` }]);
    await Promise.all(
      items.map(async (item) => {
        try {
          const st = await item.start();
          const op = st?.operation ? await finish(st.operation) : null;
          if (op && op.status !== "Success") throw new Error(op.err || op.status);
        } catch (e) {
          failed.push(`${item.name} (${e instanceof Error ? e.message : String(e)})`);
        } finally {
          finished += 1;
          update(id, { message: `${finished} of ${total} done` });
          setVersion((v) => v + 1); // lists refresh as each one lands
        }
      }),
    );
    update(
      id,
      failed.length
        ? { state: "failed", message: `${failed.length} of ${total} failed: ${failed.sort().join("; ")}` }
        : { state: "done", message: undefined },
    );
    return failed.length === 0;
  }, []);

  const value = useMemo(() => ({ run, runAll, version }), [run, runAll, version]);
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

