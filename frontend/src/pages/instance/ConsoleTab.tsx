import { lazy, Suspense, useState } from "react";
import { clearConsoleLog, fetchConsoleLog, startConsole, startExec, type InstanceFull } from "../../api";
import { useActions } from "../../actions";
import { useApi } from "../../useApi";
import { useAccess } from "../../access";

// xterm.js and spice-html5 are big; load them only when a console is opened.
// spice-html5 (LGPL) thereby stays a separately loaded, unmodified module (D24).
const TerminalView = lazy(() => import("./TerminalView"));
const VgaView = lazy(() => import("./VgaView"));

type Mode = "terminal" | "console" | "vga" | "log";

export default function ConsoleTab({ inst }: { inst: InstanceFull }) {
  const access = useAccess();
  const admin = access.can(inst.project, "operate");
  const running = inst.status === "Running";
  const vm = inst.type === "virtual-machine";
  const modes: { id: Mode; label: string }[] = [
    ...(admin && running
      ? [
          { id: "terminal" as const, label: "Terminal" },
          { id: "console" as const, label: "Text console" },
          ...(vm ? [{ id: "vga" as const, label: "Graphical console" }] : []),
        ]
      : []),
    { id: "log", label: "Console log" },
  ];
  const [mode, setMode] = useState<Mode>(modes[0].id);
  const { project, name } = inst;

  return (
    <>
      {!running && admin && <p className="muted">Start the instance to open a terminal or console.</p>}
      <div className="tabs" role="group" aria-label="Console mode">
        {modes.map((m) => (
          <button key={m.id} type="button" aria-pressed={m.id === mode} onClick={() => setMode(m.id)}>
            {m.label}
          </button>
        ))}
      </div>
      <Suspense fallback={<p role="status">Loading console…</p>}>
        {mode === "terminal" && <TerminalView label="Terminal" start={(size) => startExec(project, name, size)} />}
        {mode === "console" && (
          <TerminalView label="Text console" start={(size) => startConsole(project, name, { type: "console", ...size })} />
        )}
        {mode === "vga" && <VgaView key={`${inst.project}/${inst.name}`} inst={inst} />}
      </Suspense>
      {mode === "log" && <ConsoleLog inst={inst} />}
    </>
  );
}

function ConsoleLog({ inst }: { inst: InstanceFull }) {
  const access = useAccess();
  const { run, version } = useActions();
  const log = useApi(`console-log:${inst.project}/${inst.name}:${version}`, () => fetchConsoleLog(inst.project, inst.name));
  return (
    <section aria-label="Console log">
      {log.error && <p role="alert">{log.error}</p>}
      {access.can(inst.project, "operate") && (
        <button type="button" onClick={() => void run(`Clear ${inst.name} console log`, () => clearConsoleLog(inst.project, inst.name))}>
          Clear log
        </button>
      )}
      <pre className="log" aria-label="Console log content">
        {log.data === null ? "Loading…" : log.data || "(empty)"}
      </pre>
    </section>
  );
}
