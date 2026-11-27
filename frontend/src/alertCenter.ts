import { useEffect, useRef, useState } from "react";
import { fetchAlerts, type AlertRow } from "./api";
import { useApi } from "./useApi";

const SEEN_KEY = "mcui.alerts.seen";
const POLL_MS = 15_000;
const WARNING_TOAST_MS = 20_000;

/** An alert as announced: turning critical is news again (D91 review). */
const seenKey = (a: AlertRow) => `${a.id}:${a.severity}`;

function readSeen(): Set<string> | null {
  try {
    const raw = sessionStorage.getItem(SEEN_KEY);
    return raw ? new Set((JSON.parse(raw) as unknown[]).map(String)) : null;
  } catch {
    return null;
  }
}

function writeSeen(ids: Set<string>) {
  try {
    sessionStorage.setItem(SEEN_KEY, JSON.stringify([...ids].slice(-500)));
  } catch {
    // private mode: toasts may repeat after a reload
  }
}

export interface AlertCenter {
  open: AlertRow[];
  critical: AlertRow[];
  toasts: AlertRow[];
  dismiss: (id: number) => void;
}

/** Active alerts, polled, and the toasts for ones that fired (or turned critical) since
 * this browser tab first looked (D91). */
export function useAlertCenter(): AlertCenter {
  // the poll keeps it fresh; an acknowledgement on the Alerts page refetches there
  const { data } = useApi("alerts:active", () => fetchAlerts("active"), POLL_MS);
  const [toasts, setToasts] = useState<AlertRow[]>([]);
  const seen = useRef<Set<string> | null>(readSeen());
  const timers = useRef<number[]>([]);
  useEffect(() => () => timers.current.forEach((t) => clearTimeout(t)), []);

  useEffect(() => {
    if (!data) return;
    if (seen.current === null) {
      // first look in this browser session: what's already active isn't news
      seen.current = new Set(data.map(seenKey));
      writeSeen(seen.current);
      return;
    }
    // toasts for alerts that cleared or were acknowledged go
    const still = new Map(data.filter((a) => !a.ack_by).map((a) => [a.id, a]));
    setToasts((t) => (t.some((x) => !still.has(x.id)) ? t.filter((x) => still.has(x.id)) : t));
    const fresh = [...still.values()].filter((a) => !seen.current!.has(seenKey(a)));
    if (fresh.length === 0) return;
    fresh.forEach((a) => seen.current!.add(seenKey(a)));
    writeSeen(seen.current);
    setToasts((t) => [...t.filter((x) => !fresh.some((a) => a.id === x.id)), ...fresh]);
    for (const a of fresh.filter((x) => x.severity === "warning")) {
      timers.current.push(window.setTimeout(() => setToasts((t) => t.filter((x) => x !== a)), WARNING_TOAST_MS));
    }
  }, [data]);

  const open = (data ?? []).filter((a) => !a.ack_by);
  return {
    open,
    critical: open.filter((a) => a.severity === "critical"),
    toasts,
    dismiss: (id) => setToasts((t) => t.filter((x) => x.id !== id)),
  };
}
