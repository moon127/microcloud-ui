import { useEffect, useEffectEvent } from "react";
import { useSession } from "./session";

export interface LxdEvent {
  type: string;
  timestamp?: string;
  project?: string;
  location?: string;
  metadata?: Record<string, unknown>;
}

const RECONNECT_MS = 5000;

/** Call ``onEvent`` for every LXD operation/lifecycle event (via /api/events). */
export function useEvents(onEvent: (e: LxdEvent) => void): void {
  // the latest callbacks, without reconnecting when they change
  const handle = useEffectEvent(onEvent);
  const { onExpired } = useSession();
  const expire = useEffectEvent(() => onExpired());

  useEffect(() => {
    let ws: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let stopped = false;

    function connect() {
      const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
      ws = new WebSocket(`${proto}//${window.location.host}/api/events`);
      ws.onmessage = (m) => {
        try {
          handle(JSON.parse(String(m.data)) as LxdEvent);
        } catch {
          // ignore malformed frames
        }
      };
      ws.onclose = (e) => {
        if (stopped) return;
        if (e.code === 4401) {
          expire();
          return;
        }
        timer = setTimeout(connect, RECONNECT_MS);
      };
    }
    connect();
    return () => {
      stopped = true;
      clearTimeout(timer);
      ws?.close();
    };
  }, []);
}
