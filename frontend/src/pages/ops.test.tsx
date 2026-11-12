import { act, fireEvent, screen, within } from "@testing-library/react";
import { Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import Layout from "../Layout";
import { renderAt, session } from "../test/render";
import { FakeSocket } from "../test/fakeSocket";
import MemberPage from "./MemberPage";
import OperationsPage from "./OperationsPage";
import WarningsPage from "./WarningsPage";

afterEach(() => vi.restoreAllMocks());

const viewer = () => session({ user: { username: "v", role: "viewer" } });

const ops: api.Operation[] = [
  {
    id: "op1",
    description: "Starting instance",
    created_at: "2026-01-01T00:00:00Z",
    status: "Running",
    may_cancel: true,
    resources: { instances: ["/1.0/instances/c1"] },
    project: "default",
    location: "m1",
  },
  {
    id: "op2",
    description: "Deleting instance",
    created_at: "2026-01-01T00:00:00Z",
    status: "Failure",
    err: "busy",
    metadata: { fs_progress: "root: 10 MB" },
  },
];

describe("OperationsPage", () => {
  it("lists operations, cancels as admin and refreshes on events", async () => {
    const list = vi.spyOn(api, "fetchOperations").mockResolvedValue(ops);
    const cancel = vi.spyOn(api, "cancelOperation").mockResolvedValue();
    renderAt(<OperationsPage />);
    const row = (await screen.findByText("Starting instance")).closest("tr")!;
    expect(row).toHaveTextContent("c1");
    expect(row).toHaveTextContent("m1");
    expect(screen.getByText("Failure: busy")).toBeInTheDocument();
    expect(screen.getByText("root: 10 MB")).toBeInTheDocument();
    fireEvent.click(within(row).getByRole("button", { name: "Cancel" }));
    expect(cancel).toHaveBeenCalledWith("op1");
    expect(await screen.findByText(/✓ Cancel Starting instance/)).toBeInTheDocument();
    const before = list.mock.calls.length;
    act(() => FakeSocket.all[0].emit('{"type":"operation"}'));
    act(() => FakeSocket.all[0].emit('{"type":"lifecycle"}'));
    await vi.waitFor(() => expect(list.mock.calls.length).toBe(before + 1));
  });

  it("names objects from entity_url and nests a bulk operation's children (LXD 6)", async () => {
    vi.spyOn(api, "fetchOperations").mockResolvedValue([
      {
        id: "p",
        description: "Updating the state of multiple instances",
        created_at: "2026-11-12T18:34:48Z",
        status: "Running",
        resources: {},
        metadata: { entity_url: "/1.0/projects/default" },
        child_count: 2,
        children: [
          { id: "c1", description: "Stopping instance", created_at: "2026-11-12T18:34:48Z", status: "Success", metadata: { entity_url: "/1.0/instances/c1" } },
          { id: "c2", description: "Stopping instance", created_at: "2026-11-12T18:34:48Z", status: "Running", metadata: { entity_url: "/1.0/instances/web%2D2?project=lab" } },
        ],
      },
    ]);
    renderAt(<OperationsPage />);
    const rows = await screen.findAllByRole("row");
    expect(rows).toHaveLength(4); // header, parent, two children
    expect(rows[1]).toHaveTextContent("Updating the state of multiple instances (2 operations)");
    expect(rows[1]).toHaveTextContent("default");
    expect(rows[2]).toHaveClass("child");
    expect(rows[2]).toHaveTextContent("↳ Stopping instance");
    expect(rows[2]).toHaveTextContent("c1");
    expect(rows[3]).toHaveTextContent("web-2"); // decoded, query dropped
  });

  it("hides cancel from viewers and handles empty and error states", async () => {
    vi.spyOn(api, "fetchOperations").mockResolvedValue(ops);
    const { unmount } = renderAt(<OperationsPage />, { s: viewer() });
    await screen.findByText("Starting instance");
    expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
    unmount();
    vi.spyOn(api, "fetchOperations").mockResolvedValue([]);
    const r = renderAt(<OperationsPage />);
    expect(await screen.findByText("No recent operations.")).toBeInTheDocument();
    r.unmount();
    vi.spyOn(api, "fetchOperations").mockRejectedValue(new Error("down"));
    renderAt(<OperationsPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("down");
  });
});

const warnings: api.LxdWarning[] = [
  {
    uuid: "w1",
    type: "Offline cluster member",
    status: "new",
    severity: "moderate",
    count: 3,
    last_seen_at: "2026-01-01T00:00:00Z",
    last_message: "m3 is offline",
    location: "m1",
    project: "",
  },
  {
    uuid: "w2",
    type: "Disk low",
    status: "acknowledged",
    severity: "low",
    count: 1,
    last_seen_at: "2026-01-01T00:00:00Z",
    last_message: "x",
  },
];

describe("WarningsPage", () => {
  it("acknowledges, reopens and deletes with confirmation", async () => {
    vi.spyOn(api, "fetchWarnings").mockResolvedValue(warnings);
    const status = vi.spyOn(api, "setWarningStatus").mockResolvedValue({ operation: null, metadata: null });
    const del = vi.spyOn(api, "deleteWarning").mockResolvedValue();
    renderAt(<WarningsPage />);
    const row = (await screen.findByText("m3 is offline")).closest("tr")!;
    fireEvent.click(within(row).getByRole("button", { name: "Acknowledge" }));
    expect(status).toHaveBeenCalledWith("w1", "acknowledged");
    const row2 = screen.getByText("Disk low").closest("tr")!;
    fireEvent.click(within(row2).getByRole("button", { name: "Reopen" }));
    expect(status).toHaveBeenCalledWith("w2", "new");

    fireEvent.click(within(row).getByRole("button", { name: "Delete" }));
    const dlg = screen.getByRole("dialog", { name: "Delete warning?" });
    expect(dlg).toHaveTextContent('"Offline cluster member" on m1.');
    fireEvent.click(within(dlg).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(within(row2).getByRole("button", { name: "Delete" }));
    expect(screen.getByRole("dialog")).toHaveTextContent('"Disk low" on the cluster.');
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }));
    expect(del).toHaveBeenCalledWith("w2");
    expect(await screen.findByText(/✓ Delete warning Disk low/)).toBeInTheDocument();
  });

  it("is read-only for viewers; empty and error states", async () => {
    vi.spyOn(api, "fetchWarnings").mockResolvedValue(warnings);
    const a = renderAt(<WarningsPage />, { s: viewer() });
    await screen.findByText("m3 is offline");
    expect(screen.queryByRole("button")).toBeNull();
    a.unmount();
    vi.spyOn(api, "fetchWarnings").mockResolvedValue([]);
    const b = renderAt(<WarningsPage />);
    expect(await screen.findByText("No warnings.")).toBeInTheDocument();
    b.unmount();
    vi.spyOn(api, "fetchWarnings").mockRejectedValue(new Error("x"));
    renderAt(<WarningsPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("x");
  });
});

const member = (status: string): api.Member => ({
  name: "m1",
  url: "https://192.0.2.1:8443",
  status,
  roles: ["database"],
  failure_domain: "default",
  message: "Fully operational",
  architecture: "x86_64",
});

describe("member evacuate and restore", () => {
  function setup(status: string, s = session(), features: Record<string, boolean> = {}) {
    vi.spyOn(api, "fetchServer").mockResolvedValue({
      version: "6.10.1", clustered: true, server_name: "m1", auth: "trusted", auth_method: "tls", endpoint: "", features,
    });
    vi.spyOn(api, "fetchMemberResources").mockReturnValue(new Promise(() => {}));
    vi.spyOn(api, "queryMetric").mockReturnValue(new Promise(() => {}));
    vi.spyOn(api, "fetchMembers").mockResolvedValue([member(status), { ...member("Online"), name: "m2", roles: [] }]);
    return renderAt(<MemberPage />, { url: "/members/m1", path: "/members/:name", s });
  }

  it("evacuates with a chosen mode", async () => {
    const ev = vi.spyOn(api, "evacuateMember").mockResolvedValue({ operation: null, metadata: null });
    setup("Online");
    expect(await screen.findByText("Online")).toBeInTheDocument();
    expect(screen.getByText(/roles: database/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Evacuate" }));
    const dlg = screen.getByRole("dialog", { name: "Evacuate m1?" });
    fireEvent.change(within(dlg).getByLabelText("Mode"), { target: { value: "live-migrate" } });
    fireEvent.click(within(dlg).getByRole("button", { name: "Evacuate" }));
    expect(ev).toHaveBeenCalledWith("m1", "live-migrate");
    expect(await screen.findByText(/✓ Evacuate m1/)).toBeInTheDocument();
  });

  it("evacuates with the default mode, or cancels", async () => {
    const ev = vi.spyOn(api, "evacuateMember").mockResolvedValue({ operation: null, metadata: null });
    setup("Online");
    fireEvent.click(await screen.findByRole("button", { name: "Evacuate" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(ev).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Evacuate" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Evacuate" }));
    expect(ev).toHaveBeenCalledWith("m1", undefined);
  });

  it("offers to force an evacuation LXD refuses for quorum (LXD 6.9+)", async () => {
    const ev = vi
      .spyOn(api, "evacuateMember")
      .mockRejectedValueOnce(new Error("LXD: Insufficient online voters to maintain quorum"))
      .mockResolvedValue({ operation: null, metadata: null });
    setup("Online", session(), { evacuation_force: true });
    fireEvent.click(await screen.findByRole("button", { name: "Evacuate" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Evacuate" }));
    const dlg = await screen.findByRole("dialog", { name: "Force the evacuation of m1?" });
    expect(dlg).toHaveTextContent("Insufficient online voters to maintain quorum");
    fireEvent.click(within(dlg).getByRole("button", { name: "Force evacuation" }));
    expect(ev).toHaveBeenLastCalledWith("m1", undefined, true);
    expect(await screen.findByText(/✓ Evacuate m1 \(forced\)/)).toBeInTheDocument();
  });

  it("doesn't offer force where LXD can't (5.21) or for other errors", async () => {
    vi.spyOn(api, "evacuateMember").mockRejectedValue(new Error("LXD: Insufficient online voters to maintain quorum"));
    setup("Online");
    fireEvent.click(await screen.findByRole("button", { name: "Evacuate" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Evacuate" }));
    expect(await screen.findByText(/Insufficient online voters/)).toBeInTheDocument(); // the toast
    expect(screen.queryByRole("dialog", { name: /Force/ })).toBeNull();
  });

  it("restores an evacuated member", async () => {
    const rs = vi.spyOn(api, "restoreMember").mockResolvedValue({ operation: null, metadata: null });
    setup("Evacuated");
    fireEvent.click(await screen.findByRole("button", { name: "Restore" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByRole("button", { name: "Restore" }));
    fireEvent.click(within(screen.getByRole("dialog", { name: "Restore m1?" })).getByRole("button", { name: "Restore" }));
    expect(rs).toHaveBeenCalledWith("m1");
  });

  it("shows status without buttons to viewers, and nothing for unknown members", async () => {
    setup("Online", viewer());
    expect(await screen.findByText("Online")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Evacuate" })).toBeNull();
  });

  it("member without roles or message", async () => {
    vi.spyOn(api, "fetchMemberResources").mockReturnValue(new Promise(() => {}));
    vi.spyOn(api, "queryMetric").mockReturnValue(new Promise(() => {}));
    vi.spyOn(api, "fetchMembers").mockResolvedValue([{ ...member("Online"), name: "m2", roles: [], message: "" }]);
    renderAt(<MemberPage />, { url: "/members/m2", path: "/members/:name" });
    expect(await screen.findByText(/roles: none/)).toBeInTheDocument();
  });
});

describe("project switcher", () => {
  it("lists projects and keeps an unknown stored one", async () => {
    localStorage.setItem("mcui.project", "gone");
    vi.spyOn(api, "fetchProjects").mockResolvedValue([
      { name: "default", description: "", config: {}, used_by: [] },
      { name: "mcui-test", description: "", config: {}, used_by: [] },
    ]);
    renderAt(
      <Routes>
        <Route element={<Layout />}>
          <Route path="*" element={<p>child</p>} />
        </Route>
      </Routes>,
    );
    const select = screen.getByLabelText("Project") as HTMLSelectElement;
    await screen.findByRole("option", { name: "mcui-test" });
    expect(select.value).toBe("gone");
    expect(screen.getByRole("option", { name: "gone" })).toBeInTheDocument();
    fireEvent.change(select, { target: { value: "mcui-test" } });
    expect(select.value).toBe("mcui-test");
    expect(screen.queryByRole("option", { name: "gone" })).toBeNull();
    expect(localStorage.getItem("mcui.project")).toBe("mcui-test");
    localStorage.removeItem("mcui.project");
  });
});
