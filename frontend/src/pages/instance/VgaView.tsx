import { SpiceMainConn, sendCtrlAltDel } from "@spice-project/spice-html5";
import { useEffect, useRef, useState } from "react";
import { startConsole, streamUrl, type InstanceFull } from "../../api";

type Status = "connecting" | "connected" | "failed";

/** The VM's graphical console over SPICE (IN-9). */
export default function VgaView({ inst }: { inst: InstanceFull }) {
  const conn = useRef<SpiceMainConn | null>(null);
  const [status, setStatus] = useState<Status>("connecting");
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let closed = false;
    setStatus("connecting");
    setError(null);
    const fail = (e: unknown) => {
      if (closed) return;
      setStatus("failed");
      setError(e instanceof Error ? e.message : String(e));
    };
    startConsole(inst.project, inst.name, { type: "vga" })
      .then((s) => {
        if (closed) return;
        conn.current = new SpiceMainConn({
          uri: streamUrl(s.id),
          screen_id: "spice-screen",
          onsuccess: () => !closed && setStatus("connected"),
          onerror: fail,
        });
      })
      .catch(fail);
    return () => {
      closed = true;
      conn.current?.stop();
      conn.current = null;
    };
  }, [inst.project, inst.name, attempt]);

  return (
    <section aria-label="Graphical console" className="vga">
      <div className="actions">
        <span role="status" className="muted">
          {status === "connecting" && "Connecting…"}
          {status === "connected" && "Connected."}
          {status === "failed" && `Could not connect: ${error}`}
        </span>
        <button type="button" disabled={status !== "connected"} onClick={() => conn.current && sendCtrlAltDel(conn.current)}>
          Send Ctrl+Alt+Del
        </button>
        <button type="button" onClick={() => setAttempt((a) => a + 1)}>
          Reconnect
        </button>
        <button
          type="button"
          onClick={() => void document.getElementById("spice-screen")?.requestFullscreen?.()}
        >
          Full screen
        </button>
      </div>
      <div id="spice-screen" className="spice-screen" data-testid="spice-screen" />
    </section>
  );
}
