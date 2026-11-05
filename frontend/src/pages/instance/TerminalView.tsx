import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { useEffect, useRef, useState } from "react";
import { streamUrl, type StreamStart } from "../../api";

interface Props {
  /** Start the LXD session for the given terminal size. */
  start: (size: { cols: number; rows: number }) => Promise<StreamStart>;
  label: string;
}

type Status = "connecting" | "connected" | "ended" | "failed";

/** An xterm.js terminal attached to an exec or text-console stream. */
export default function TerminalView({ start, label }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState<Status>("connecting");
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const startRef = useRef(start);
  startRef.current = start;

  useEffect(() => {
    const el = box.current!;
    const term = new Terminal({ cursorBlink: true, convertEol: false, fontSize: 13 });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(el);
    fit.fit();
    let ws: WebSocket | null = null;
    let closed = false;
    const encoder = new TextEncoder();
    setStatus("connecting");
    setError(null);

    startRef
      .current({ cols: term.cols, rows: term.rows })
      .then((s) => {
        if (closed) return;
        ws = new WebSocket(streamUrl(s.id));
        ws.binaryType = "arraybuffer";
        ws.onopen = () => setStatus("connected");
        // binaryType is arraybuffer, so anything that isn't text is binary output
        ws.onmessage = (m) => term.write(typeof m.data === "string" ? m.data : new Uint8Array(m.data as ArrayBuffer));
        ws.onclose = () => {
          if (closed) return;
          setStatus("ended");
          term.write("\r\n\x1b[2m[session ended]\x1b[0m\r\n");
        };
        term.onData((d) => ws?.readyState === WebSocket.OPEN && ws.send(encoder.encode(d)));
        term.onResize(({ cols, rows }) => {
          if (ws?.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ command: "window-resize", args: { width: String(cols), height: String(rows) } }));
          }
        });
        term.focus();
      })
      .catch((e: unknown) => {
        if (closed) return;
        setStatus("failed");
        setError(e instanceof Error ? e.message : String(e));
      });

    const ro = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => fit.fit());
    ro?.observe(el);
    return () => {
      closed = true;
      ro?.disconnect();
      ws?.close();
      term.dispose();
    };
  }, [attempt]);

  return (
    <section aria-label={label} className="terminal">
      <p className="muted" role="status">
        {status === "connecting" && "Connecting…"}
        {status === "connected" && "Connected."}
        {status === "ended" && "Session ended."}
        {status === "failed" && `Could not start: ${error}`}{" "}
        {(status === "ended" || status === "failed") && (
          <button type="button" onClick={() => setAttempt((a) => a + 1)}>
            Reconnect
          </button>
        )}
      </p>
      <div ref={box} className="xterm-box" data-testid="terminal" />
    </section>
  );
}
