import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { renderAt, session } from "../../test/render";
import { FakeSocket } from "../../test/fakeSocket";
import ConsoleTab from "./ConsoleTab";
import TerminalView from "./TerminalView";
import VgaView from "./VgaView";

// --- test doubles for xterm.js and spice-html5 (jsdom can't run them) ----------
const { terms, fits, FakeTerm } = vi.hoisted(() => {
  const terms: InstanceType<typeof FakeTerm>[] = [];
  const fits: number[] = [];
  class FakeTerm {
    cols = 80;
    rows = 24;
    written: unknown[] = [];
    disposed = false;
    focused = false;
    dataCb: (d: string) => void = () => {};
    resizeCb: (s: { cols: number; rows: number }) => void = () => {};
    constructor() {
      terms.push(this);
    }
    loadAddon() {}
    open() {}
    write(d: unknown) {
      this.written.push(d);
    }
    onData(cb: (d: string) => void) {
      this.dataCb = cb;
    }
    onResize(cb: (s: { cols: number; rows: number }) => void) {
      this.resizeCb = cb;
    }
    focus() {
      this.focused = true;
    }
    dispose() {
      this.disposed = true;
    }
  }
  return { terms, fits, FakeTerm };
});
vi.mock("@xterm/xterm", () => ({ Terminal: FakeTerm }));
vi.mock("@xterm/addon-fit", () => ({
  FitAddon: class {
    fit() {
      fits.push(1);
    }
  },
}));

const spice = vi.hoisted(() => ({ conns: [] as { o: Record<string, (e?: unknown) => void> & { uri: string }; stopped: boolean }[], cad: vi.fn() }));
vi.mock("@spice-project/spice-html5", () => ({
  SpiceMainConn: class {
    o: never;
    stopped = false;
    constructor(o: never) {
      this.o = o;
      spice.conns.push(this as never);
    }
    stop() {
      this.stopped = true;
    }
  },
  sendCtrlAltDel: spice.cad,
}));

beforeEach(() => {
  terms.length = 0;
  fits.length = 0;
  spice.conns.length = 0;
  spice.cad.mockReset();
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const started = (id = "s1"): api.StreamStart => ({ id, kind: "exec", operation: "op1" });

describe("TerminalView", () => {
  it("starts a session sized to the terminal and relays both ways", async () => {
    const start = vi.fn().mockResolvedValue(started());
    const { unmount } = render(<TerminalView label="Terminal" start={start} />);
    expect(screen.getByRole("status")).toHaveTextContent("Connecting");
    await waitFor(() => expect(FakeSocket.all).toHaveLength(1));
    expect(start).toHaveBeenCalledWith({ cols: 80, rows: 24 });
    const ws = FakeSocket.all[0];
    expect(ws.url).toBe(`ws://${window.location.host}/api/streams/s1`);
    expect(ws.binaryType).toBe("arraybuffer");
    act(() => ws.open());
    expect(screen.getByRole("status")).toHaveTextContent("Connected.");
    const term = terms[0];
    expect(term.focused).toBe(true);
    ws.emit(new TextEncoder().encode("hi").buffer);
    ws.emit("text");
    expect(term.written[0]).toEqual(new Uint8Array([104, 105]));
    expect(term.written[1]).toBe("text");
    term.dataCb("ls\r");
    expect(ws.sent[0]).toEqual(new TextEncoder().encode("ls\r"));
    term.resizeCb({ cols: 100, rows: 30 });
    expect(JSON.parse(ws.sent[1] as string)).toEqual({ command: "window-resize", args: { width: "100", height: "30" } });
    ws.readyState = 0; // not open: input and resizes are dropped
    term.dataCb("x");
    term.resizeCb({ cols: 1, rows: 1 });
    expect(ws.sent).toHaveLength(2);
    unmount();
    expect(ws.closed).toBe(true);
    expect(term.disposed).toBe(true);
  });

  it("reports the end of a session and reconnects", async () => {
    const start = vi.fn().mockResolvedValue(started());
    render(<TerminalView label="T" start={start} />);
    await waitFor(() => expect(FakeSocket.all).toHaveLength(1));
    act(() => FakeSocket.all[0].drop());
    expect(screen.getByRole("status")).toHaveTextContent("Session ended.");
    expect(terms[0].written.at(-1)).toContain("[session ended]");
    fireEvent.click(screen.getByRole("button", { name: "Reconnect" }));
    await waitFor(() => expect(FakeSocket.all).toHaveLength(2));
    expect(start).toHaveBeenCalledTimes(2);
    expect(terms[0].disposed).toBe(true);
  });

  it("shows start failures; ignores results after unmount", async () => {
    const { unmount } = render(<TerminalView label="T" start={() => Promise.reject(new Error("Instance is not running"))} />);
    expect(await screen.findByText(/Could not start: Instance is not running/)).toBeInTheDocument();
    unmount();
    let resolve!: (s: api.StreamStart) => void;
    let reject!: (e: unknown) => void;
    const a = render(<TerminalView label="T" start={() => new Promise((r) => (resolve = r))} />);
    a.unmount();
    resolve(started());
    const b = render(<TerminalView label="T" start={() => new Promise((_, r) => (reject = r))} />);
    b.unmount();
    reject("late");
    await Promise.resolve();
    expect(FakeSocket.all).toHaveLength(0);
  });

  it("ignores a socket closing after unmount and fits on resize", async () => {
    let cb: () => void = () => {};
    vi.stubGlobal(
      "ResizeObserver",
      class {
        constructor(fn: () => void) {
          cb = fn;
        }
        observe() {}
        disconnect() {}
      },
    );
    vi.stubGlobal("WebSocket", FakeSocket);
    const { unmount } = render(<TerminalView label="T" start={() => Promise.resolve(started())} />);
    await waitFor(() => expect(FakeSocket.all).toHaveLength(1));
    cb();
    expect(fits.length).toBeGreaterThanOrEqual(2);
    unmount();
    FakeSocket.all[0].drop();
    expect(terms[0].written.join("")).not.toContain("[session ended]");
  });
});

const inst = (over: Partial<api.InstanceFull> = {}): api.InstanceFull => ({
  project: "default",
  name: "v1",
  type: "virtual-machine",
  status: "Running",
  location: "m1",
  description: "",
  profiles: [],
  config: {},
  devices: {},
  ...over,
});

describe("VgaView", () => {
  it("connects SPICE to the stream and sends Ctrl+Alt+Del", async () => {
    const start = vi.spyOn(api, "startConsole").mockResolvedValue({ ...started("vga1"), kind: "vga" });
    const { unmount } = render(<VgaView inst={inst()} />);
    await waitFor(() => expect(spice.conns).toHaveLength(1));
    expect(start).toHaveBeenCalledWith("default", "v1", { type: "vga" });
    const c = spice.conns[0];
    expect(c.o.uri).toBe(`ws://${window.location.host}/api/streams/vga1`);
    expect(c.o.screen_id).toBe("spice-screen");
    expect(screen.getByRole("button", { name: "Send Ctrl+Alt+Del" })).toBeDisabled();
    act(() => c.o.onsuccess());
    expect(screen.getByRole("status")).toHaveTextContent("Connected.");
    fireEvent.click(screen.getByRole("button", { name: "Send Ctrl+Alt+Del" }));
    expect(spice.cad).toHaveBeenCalledWith(c);
    const fs = vi.fn().mockResolvedValue(undefined);
    (document.getElementById("spice-screen") as HTMLElement).requestFullscreen = fs;
    fireEvent.click(screen.getByRole("button", { name: "Full screen" }));
    expect(fs).toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Reconnect" }));
    await waitFor(() => expect(spice.conns).toHaveLength(2));
    expect(c.stopped).toBe(true);
    unmount();
    expect(spice.conns[1].stopped).toBe(true);
  });

  it("reports SPICE and start errors; late results are ignored", async () => {
    vi.spyOn(api, "startConsole").mockResolvedValue(started("v"));
    const a = render(<VgaView inst={inst()} />);
    await waitFor(() => expect(spice.conns).toHaveLength(1));
    act(() => spice.conns[0].o.onerror(new Error("handshake failed")));
    expect(screen.getByRole("status")).toHaveTextContent("Could not connect: handshake failed");
    a.unmount();
    spice.conns[0].o.onerror("late"); // after unmount: ignored
    spice.conns[0].o.onsuccess();
    vi.spyOn(api, "startConsole").mockRejectedValue("denied");
    render(<VgaView inst={inst()} />);
    expect(await screen.findByText("Could not connect: denied")).toBeInTheDocument();
  });

  it("ignores a start that finishes after unmount; full screen is optional", async () => {
    let resolve!: (s: api.StreamStart) => void;
    vi.spyOn(api, "startConsole").mockReturnValue(new Promise((r) => (resolve = r)));
    const { unmount } = render(<VgaView inst={inst()} />);
    (document.getElementById("spice-screen") as HTMLElement).requestFullscreen = undefined as never;
    fireEvent.click(screen.getByRole("button", { name: "Full screen" }));
    unmount();
    resolve(started());
    await Promise.resolve();
    expect(spice.conns).toHaveLength(0);
  });
});

describe("ConsoleTab", () => {
  it("offers terminal, text and graphical console for running VMs", async () => {
    vi.spyOn(api, "startExec").mockResolvedValue(started());
    const con = vi.spyOn(api, "startConsole").mockResolvedValue(started("c"));
    vi.spyOn(api, "fetchConsoleLog").mockResolvedValue("");
    renderAt(<ConsoleTab inst={inst()} />);
    expect(await screen.findByRole("region", { name: "Terminal" })).toBeInTheDocument();
    await waitFor(() => expect(api.startExec).toHaveBeenCalledWith("default", "v1", { cols: 80, rows: 24 }));
    fireEvent.click(screen.getByRole("button", { name: "Text console" }));
    await waitFor(() => expect(con).toHaveBeenCalledWith("default", "v1", { type: "console", cols: 80, rows: 24 }));
    fireEvent.click(screen.getByRole("button", { name: "Graphical console" }));
    expect(await screen.findByRole("region", { name: "Graphical console" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Console log" }));
    expect(await screen.findByText("(empty)")).toBeInTheDocument();
  });

  it("containers have no graphical console; stopped instances only the log", async () => {
    vi.spyOn(api, "startExec").mockReturnValue(new Promise(() => {}));
    vi.spyOn(api, "fetchConsoleLog").mockResolvedValue("boot ok\n");
    const a = renderAt(<ConsoleTab inst={inst({ type: "container" })} />);
    expect(screen.queryByRole("button", { name: "Graphical console" })).toBeNull();
    a.unmount();
    renderAt(<ConsoleTab inst={inst({ status: "Stopped" })} />);
    expect(screen.getByText(/Start the instance/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Terminal" })).toBeNull();
    expect(await screen.findByText("boot ok")).toBeInTheDocument();
  });

  it("viewers see only the log; admins can clear it; errors shown", async () => {
    vi.spyOn(api, "fetchConsoleLog").mockResolvedValue("x");
    const clear = vi.spyOn(api, "clearConsoleLog").mockResolvedValue({ operation: null, metadata: null });
    const a = renderAt(<ConsoleTab inst={inst()} />, { s: session({ user: { username: "v", role: "viewer" } }) });
    expect(screen.queryByRole("button", { name: "Terminal" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Clear log" })).toBeNull();
    expect(await screen.findByText("x")).toBeInTheDocument();
    a.unmount();
    renderAt(<ConsoleTab inst={inst({ status: "Stopped" })} />);
    fireEvent.click(await screen.findByRole("button", { name: "Clear log" }));
    expect(clear).toHaveBeenCalledWith("default", "v1");
    vi.spyOn(api, "fetchConsoleLog").mockRejectedValue(new Error("gone"));
    renderAt(<ConsoleTab inst={inst({ status: "Stopped", name: "v2" })} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("gone");
  });
});
