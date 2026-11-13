import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useActions, type RunOptions } from "./actions";
import ActionsProvider from "./ActionsProvider";
import * as api from "./api";
import Confirm from "./Confirm";
import { useEvents, type LxdEvent } from "./events";
import { useProject } from "./project";
import { ProjectProvider } from "./ProjectProvider";
import { SessionContext } from "./session";
import { session } from "./test/render";
import { FakeSocket } from "./test/fakeSocket";
import YamlEditor from "./YamlEditor";

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const op = (status: string, extra: Partial<api.Operation> = {}): api.Operation => ({
  id: "op1",
  description: "Starting instance",
  created_at: "2026-01-01T00:00:00Z",
  status,
  ...extra,
});

function Runner({
  start,
  label = "Start c1",
  opts,
}: {
  start: () => Promise<api.Started | void>;
  label?: string;
  opts?: RunOptions;
}) {
  const { run, version } = useActions();
  return (
    <>
      <button type="button" onClick={() => void run(label, start, opts)}>
        go
      </button>
      <p>version={version}</p>
    </>
  );
}

function AllRunner({ items }: { items: { name: string; start: () => Promise<api.Started | void> }[] }) {
  const { runAll, version } = useActions();
  const [result, setResult] = useState<string>("");
  return (
    <>
      <button type="button" onClick={() => void runAll("Stop 3 instances", items).then((ok) => setResult(String(ok)))}>
        all
      </button>
      <p>version={version}</p>
      <p>result={result}</p>
    </>
  );
}

describe("ActionsProvider", () => {
  it("runs several changes under one toast that counts them off and names failures", async () => {
    let finishB: (op: api.Operation) => void = () => {};
    vi.spyOn(api, "waitOperation").mockImplementation((id) =>
      id === "b" ? new Promise((r) => (finishB = r)) : Promise.resolve(op("Failure", { id, err: "disk full" })),
    );
    render(
      <ActionsProvider>
        <AllRunner
          items={[
            { name: "a", start: () => Promise.resolve({ operation: null, metadata: null }) },
            { name: "b", start: () => Promise.resolve({ operation: op("Running", { id: "b" }), metadata: null }) },
            { name: "c", start: () => Promise.resolve({ operation: op("Running", { id: "c" }), metadata: null }) },
          ]}
        />
      </ActionsProvider>,
    );
    fireEvent.click(screen.getByText("all"));
    // one toast for all three, counting: a is done at once, c fails, b is still going
    expect(await screen.findByText("⏳ Stop 3 instances: 2 of 3 done")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
    await act(async () => finishB(op("Success", { id: "b" })));
    expect(await screen.findByText("✗ Stop 3 instances: 1 of 3 failed: c (disk full)")).toBeInTheDocument();
    expect(screen.getByText("result=false")).toBeInTheDocument();
    expect(screen.getByText("version=3")).toBeInTheDocument(); // a refresh as each lands
  });

  it("a bulk run that all succeeds; a request that's refused", async () => {
    const { rerender } = render(
      <ActionsProvider>
        <AllRunner items={[{ name: "a", start: () => Promise.resolve() }]} />
      </ActionsProvider>,
    );
    fireEvent.click(screen.getByText("all"));
    expect(await screen.findByText("✓ Stop 3 instances")).toBeInTheDocument();
    expect(screen.getByText("result=true")).toBeInTheDocument();
    rerender(
      <ActionsProvider>
        <AllRunner items={[{ name: "x", start: () => Promise.reject(new api.ApiError(403, "admin role required")) }, { name: "y", start: () => Promise.reject("nope") }]} />
      </ActionsProvider>,
    );
    fireEvent.click(screen.getByText("all"));
    expect(await screen.findByText("✗ Stop 3 instances: 2 of 2 failed: x (admin role required); y (nope)")).toBeInTheDocument();
  });

  it("follows an operation until it succeeds", async () => {
    const wait = vi
      .spyOn(api, "waitOperation")
      .mockResolvedValueOnce(op("Running"))
      .mockResolvedValueOnce(op("Success"));
    render(
      <ActionsProvider>
        <Runner start={() => Promise.resolve({ operation: op("Pending"), metadata: null })} />
      </ActionsProvider>,
    );
    fireEvent.click(screen.getByText("go"));
    expect(screen.getByText(/⏳ Start c1/)).toBeInTheDocument();
    expect(await screen.findByText(/✓ Start c1/)).toBeInTheDocument();
    expect(wait).toHaveBeenCalledTimes(2);
    expect(screen.getByText("version=1")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss Start c1" }));
    expect(screen.queryByText(/Start c1/)).toBeNull();
  });

  it("reports failed, cancelled and thrown actions", async () => {
    vi.spyOn(api, "waitOperation").mockResolvedValue(op("Failure", { err: "disk full" }));
    const { rerender } = render(
      <ActionsProvider>
        <Runner start={() => Promise.resolve({ operation: op("Running"), metadata: null })} />
      </ActionsProvider>,
    );
    fireEvent.click(screen.getByText("go"));
    expect(await screen.findByText("✗ Start c1: disk full")).toBeInTheDocument();

    rerender(
      <ActionsProvider>
        <Runner label="B" start={() => Promise.resolve({ operation: op("Cancelled"), metadata: null })} />
      </ActionsProvider>,
    );
    fireEvent.click(screen.getByText("go"));
    expect(await screen.findByText("✗ B: Cancelled")).toBeInTheDocument();

    rerender(
      <ActionsProvider>
        <Runner label="C" start={() => Promise.reject(new api.ApiError(403, "admin role required"))} />
      </ActionsProvider>,
    );
    fireEvent.click(screen.getByText("go"));
    expect(await screen.findByText("✗ C: admin role required")).toBeInTheDocument();

    rerender(
      <ActionsProvider>
        <Runner label="D" start={() => Promise.reject("nope")} />
      </ActionsProvider>,
    );
    fireEvent.click(screen.getByText("go"));
    expect(await screen.findByText("✗ D: nope")).toBeInTheDocument();
  });

  it("reports progress with short waits, and errors", async () => {
    const wait = vi.spyOn(api, "waitOperation").mockResolvedValueOnce(op("Running")).mockResolvedValueOnce(op("Failure", { err: "no space" }));
    const onProgress = vi.fn();
    const onError = vi.fn();
    render(
      <ActionsProvider>
        <Runner start={() => Promise.resolve({ operation: op("Pending"), metadata: null })} opts={{ onProgress, onError }} />
      </ActionsProvider>,
    );
    fireEvent.click(screen.getByText("go"));
    expect(await screen.findByText("✗ Start c1: no space")).toBeInTheDocument();
    expect(wait).toHaveBeenCalledWith("op1", 2);
    expect(onProgress.mock.calls.map(([o]) => o.status)).toEqual(["Pending", "Running", "Failure"]);
    expect(onError).toHaveBeenCalledWith("no space");
  });

  it("treats a call without an operation as done", async () => {
    render(
      <ActionsProvider>
        <Runner start={() => Promise.resolve()} />
      </ActionsProvider>,
    );
    fireEvent.click(screen.getByText("go"));
    expect(await screen.findByText(/✓ Start c1/)).toBeInTheDocument();
  });

  it("needs a provider", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => render(<Runner start={() => Promise.resolve()} />)).toThrow("useActions outside ActionsProvider");
  });
});

describe("Confirm", () => {
  it("focuses Cancel, confirms, and cancels on Escape", () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(
      <Confirm title="Delete it?" confirmLabel="Delete" danger onConfirm={onConfirm} onCancel={onCancel}>
        <p>body</p>
      </Confirm>,
    );
    expect(screen.getByRole("dialog", { name: "Delete it?" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(onConfirm).toHaveBeenCalled();
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Enter" });
    expect(onCancel).not.toHaveBeenCalled();
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(onCancel).toHaveBeenCalled();
  });

  it("uses the primary style when not dangerous", () => {
    render(<Confirm title="t" confirmLabel="OK" onConfirm={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByRole("button", { name: "OK" })).toHaveClass("primary");
  });
});

function EventsProbe({ onEvent }: { onEvent: (e: LxdEvent) => void }) {
  useEvents(onEvent);
  return null;
}

describe("useEvents", () => {
  beforeEach(() => {
    FakeSocket.all = [];
    vi.stubGlobal("WebSocket", FakeSocket);
  });

  it("parses events, ignores junk and reconnects after drops", async () => {
    vi.useFakeTimers();
    const got: LxdEvent[] = [];
    const s = session();
    const { unmount } = render(
      <SessionContext.Provider value={s}>
        <EventsProbe onEvent={(e) => got.push(e)} />
      </SessionContext.Provider>,
    );
    const ws = FakeSocket.all[0];
    expect(ws.url).toBe(`ws://${window.location.host}/api/events`);
    ws.emit('{"type":"lifecycle","project":"p"}');
    ws.emit("not json");
    expect(got).toEqual([{ type: "lifecycle", project: "p" }]);
    ws.drop();
    await act(() => vi.advanceTimersByTimeAsync(5000));
    expect(FakeSocket.all).toHaveLength(2);
    FakeSocket.all[1].drop(4401);
    expect(s.onExpired).toHaveBeenCalled();
    await act(() => vi.advanceTimersByTimeAsync(10000));
    expect(FakeSocket.all).toHaveLength(2);
    unmount();
    expect(FakeSocket.all[1].closed).toBe(true);
    FakeSocket.all[1].drop(); // late close after unmount: no reconnect
    await act(() => vi.advanceTimersByTimeAsync(10000));
    expect(FakeSocket.all).toHaveLength(2);
  });

  it("uses wss on https", () => {
    vi.stubGlobal("location", { ...window.location, protocol: "https:", host: "ui.example" });
    render(
      <SessionContext.Provider value={session()}>
        <EventsProbe onEvent={vi.fn()} />
      </SessionContext.Provider>,
    );
    expect(FakeSocket.all[0].url).toBe("wss://ui.example/api/events");
  });
});

describe("YamlEditor", () => {
  it("emits parsed values, flags errors and follows external changes", () => {
    const onChange = vi.fn();
    const { rerender } = render(<YamlEditor label="Config" value={{ a: 1 }} onChange={onChange} />);
    const box = screen.getByLabelText("Config") as HTMLTextAreaElement;
    expect(box.value).toBe("a: 1\n");
    fireEvent.change(box, { target: { value: "a: 2\nb: [x]\n" } });
    expect(onChange).toHaveBeenLastCalledWith({ a: 2, b: ["x"] });
    fireEvent.change(box, { target: { value: "a: [" } });
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(box).toHaveAttribute("aria-invalid", "true");
    expect(onChange).toHaveBeenCalledTimes(1);
    // the same value from outside keeps the user's text; a new value replaces it
    fireEvent.change(box, { target: { value: "a: 2   # mine\nb: [x]\n" } });
    rerender(<YamlEditor label="Config" value={{ a: 2, b: ["x"] }} onChange={onChange} />);
    expect(box.value).toBe("a: 2   # mine\nb: [x]\n");
    rerender(<YamlEditor label="Config" value={{ c: true }} onChange={onChange} />);
    expect(box.value).toBe("c: true\n");
    // invalid text is replaced by a new external value
    fireEvent.change(box, { target: { value: "c: [" } });
    rerender(<YamlEditor label="Config" value={{ d: 1 }} onChange={onChange} />);
    expect(box.value).toBe("d: 1\n");
  });

  it("can be read-only", () => {
    render(<YamlEditor label="X" value={{}} onChange={vi.fn()} readOnly rows={3} />);
    expect(screen.getByLabelText("X")).toHaveAttribute("readonly");
  });
});

function ProjectProbe() {
  const { project, setProject } = useProject();
  return (
    <button type="button" onClick={() => setProject("lab")}>
      project={project || "all"}
    </button>
  );
}

describe("ProjectProvider", () => {
  it("remembers the project", async () => {
    localStorage.removeItem("mcui.project");
    const { unmount } = render(
      <ProjectProvider>
        <ProjectProbe />
      </ProjectProvider>,
    );
    fireEvent.click(screen.getByText("project=all"));
    expect(screen.getByText("project=lab")).toBeInTheDocument();
    unmount();
    render(
      <ProjectProvider>
        <ProjectProbe />
      </ProjectProvider>,
    );
    expect(screen.getByText("project=lab")).toBeInTheDocument();
    localStorage.removeItem("mcui.project");
  });

  it("works when storage is unavailable", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("denied");
    });
    render(
      <ProjectProvider>
        <ProjectProbe />
      </ProjectProvider>,
    );
    fireEvent.click(screen.getByText("project=all"));
    await waitFor(() => expect(screen.getByText("project=lab")).toBeInTheDocument());
  });

  it("defaults to all projects without a provider", () => {
    render(<ProjectProbe />);
    fireEvent.click(screen.getByText("project=all")); // no-op setter
    expect(screen.getByText("project=all")).toBeInTheDocument();
  });
});
