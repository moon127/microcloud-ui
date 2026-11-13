import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { renderAt, session } from "../../test/render";
import InstancesPage from "../InstancesPage";
import InstancePage from "./InstancePage";
import { asConfig, asDevices, stateActions } from "./state";

const ok: api.Started = { operation: null, metadata: null };
const viewer = () => session({ user: { username: "v", role: "viewer" } });

const full = (over: Partial<api.InstanceFull> = {}): api.InstanceFull => ({
  project: "default",
  name: "c1",
  type: "container",
  status: "Running",
  location: "m1",
  description: "web",
  profiles: ["default"],
  config: { "limits.cpu": "2", "volatile.base_image": "abc", "snapshots.schedule": "@daily" },
  devices: { root: { type: "disk", path: "/", pool: "remote" } },
  expanded_devices: { root: { type: "disk", path: "/", pool: "remote" }, eth0: { type: "nic", network: "n" } },
  snapshots: [
    { name: "s1", created_at: "2026-01-01T00:00:00Z", expires_at: "0001-01-01T00:00:00Z", stateful: false },
    { name: "s2", created_at: "2026-02-01T00:00:00Z", expires_at: "2026-03-01T00:00:00Z", stateful: true },
  ],
  ...over,
});

beforeEach(() => {
  vi.spyOn(api, "queryMetric").mockReturnValue(new Promise(() => {}));
  vi.spyOn(api, "fetchMembers").mockResolvedValue([
    { name: "m1", url: "", status: "Online", roles: [], failure_domain: "", message: "", architecture: "" },
    { name: "m2", url: "", status: "Online", roles: [], failure_domain: "", message: "", architecture: "" },
  ]);
  vi.spyOn(api, "fetchStoragePools").mockResolvedValue([{ name: "remote", driver: "ceph", status: "Created", description: "" }]);
});
afterEach(() => vi.restoreAllMocks());

function open(inst = full(), { tab = "", s = session() } = {}) {
  vi.spyOn(api, "fetchInstance").mockResolvedValue(inst);
  return renderAt(
    <Routes>
      <Route path="/instances/:project/:name" element={<InstancePage />} />
      <Route path="/instances" element={<p>instance list</p>} />
    </Routes>,
    { url: `/instances/${inst.project}/${inst.name}${tab ? `?tab=${tab}` : ""}`, s },
  );
}

const dialog = () => within(screen.getByRole("dialog"));

describe("state helpers", () => {
  it("offers lifecycle actions per status", () => {
    expect(stateActions("Running")).toEqual(["stop", "restart", "freeze"]);
    expect(stateActions("Frozen")).toEqual(["unfreeze", "stop"]);
    expect(stateActions("Stopped")).toEqual(["start"]);
  });

  it("coerces YAML into LXD config and devices", () => {
    expect(asConfig({ a: 1, b: true, c: null, d: "x" })).toEqual({ a: "1", b: "true", c: "", d: "x" });
    expect(asConfig(null)).toEqual({});
    expect(asConfig([1])).toMatch(/mapping/);
    expect(asConfig({ a: { b: 1 } })).toMatch(/^a: values must be/);
    expect(asDevices({ root: { type: "disk", size: 10 } })).toEqual({ root: { type: "disk", size: "10" } });
    expect(asDevices(undefined)).toEqual({});
    expect(asDevices("x")).toMatch(/mapping of device names/);
    expect(asDevices({ eth0: { network: "n" } })).toBe("eth0: every device needs a type");
    expect(asDevices({ eth0: [1] })).toMatch(/^eth0: config must be/);
  });
});

describe("instance actions", () => {
  it("runs lifecycle actions for a running instance", async () => {
    const state = vi.spyOn(api, "changeState").mockResolvedValue(ok);
    open();
    const bar = within(await screen.findByRole("toolbar", { name: "Instance actions" }));
    expect(bar.queryByRole("button", { name: "Start" })).toBeNull();
    fireEvent.click(bar.getByRole("button", { name: "Restart" }));
    fireEvent.click(bar.getByRole("button", { name: "Force stop" }));
    expect(state.mock.calls).toEqual([
      ["default", "c1", "restart"],
      ["default", "c1", "stop", true],
    ]);
  });

  it("deletes (stopping first) and returns to the list", async () => {
    const del = vi.spyOn(api, "deleteInstance").mockResolvedValue(ok);
    open();
    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
    expect(dialog().getByText(/will be stopped first/)).toBeInTheDocument();
    fireEvent.click(dialog().getByRole("button", { name: "Stop and delete" }));
    expect(del).toHaveBeenCalledWith("default", "c1", true);
    expect(await screen.findByText("instance list")).toBeInTheDocument();
  });

  it("deletes a stopped instance without force; a failure stays on the page", async () => {
    const del = vi.spyOn(api, "deleteInstance").mockRejectedValue(new Error("busy"));
    open(full({ status: "Stopped" }));
    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
    fireEvent.click(dialog().getByRole("button", { name: "Delete" }));
    expect(del).toHaveBeenCalledWith("default", "c1", false);
    expect(await screen.findByText("✗ Delete c1: busy")).toBeInTheDocument();
    expect(screen.queryByText("instance list")).toBeNull();
  });

  it("renames and follows the new name", async () => {
    const ren = vi.spyOn(api, "renameInstance").mockResolvedValue(ok);
    open(full({ status: "Stopped", project: "lab" }));
    fireEvent.click(await screen.findByRole("button", { name: "Rename" }));
    fireEvent.click(dialog().getByRole("button", { name: "OK" })); // empty: ignored
    expect(ren).not.toHaveBeenCalled();
    fireEvent.change(dialog().getByLabelText("New name"), { target: { value: " c9 " } });
    fireEvent.click(dialog().getByRole("button", { name: "OK" }));
    expect(ren).toHaveBeenCalledWith("lab", "c1", "c9");
    await waitFor(() => expect(api.fetchInstance).toHaveBeenLastCalledWith("lab", "c9"));
  });

  it("warns that renaming needs a stopped instance; rename failure stays", async () => {
    vi.spyOn(api, "renameInstance").mockRejectedValue(new Error("Instance must be stopped"));
    open();
    fireEvent.click(await screen.findByRole("button", { name: "Rename" }));
    expect(dialog().getByText("LXD only renames stopped instances.")).toBeInTheDocument();
    fireEvent.change(dialog().getByLabelText("New name"), { target: { value: "x" } });
    fireEvent.click(dialog().getByRole("button", { name: "OK" }));
    expect(await screen.findByText(/✗ Rename c1 to x/)).toBeInTheDocument();
  });

  it("copies to a member, optionally started", async () => {
    const copy = vi.spyOn(api, "copyInstance").mockResolvedValue(ok);
    open();
    fireEvent.click(await screen.findByRole("button", { name: "Copy" }));
    await dialog().findByRole("option", { name: "m2" });
    fireEvent.change(dialog().getByLabelText("New name"), { target: { value: "" } });
    fireEvent.click(dialog().getByRole("button", { name: "Copy" })); // empty name: ignored
    fireEvent.change(dialog().getByLabelText("New name"), { target: { value: "c1b" } });
    fireEvent.change(dialog().getByLabelText("Member"), { target: { value: "m2" } });
    fireEvent.click(dialog().getByLabelText("Start the copy"));
    fireEvent.click(dialog().getByRole("button", { name: "Copy" }));
    expect(copy).toHaveBeenCalledWith("default", "c1", { name: "c1b", target: "m2", start: true });
  });

  it("copies with defaults", async () => {
    const copy = vi.spyOn(api, "copyInstance").mockResolvedValue(ok);
    open();
    fireEvent.click(await screen.findByRole("button", { name: "Copy" }));
    fireEvent.click(dialog().getByRole("button", { name: "Copy" }));
    expect(copy).toHaveBeenCalledWith("default", "c1", { name: "c1-copy", target: undefined, start: false });
  });

  it("moves to a member or pool; VMs can live-migrate", async () => {
    const move = vi.spyOn(api, "moveInstance").mockResolvedValue(ok);
    open(full({ type: "virtual-machine" }));
    fireEvent.click(await screen.findByRole("button", { name: "Move" }));
    expect(dialog().getByText(/or use live migration/)).toBeInTheDocument();
    await dialog().findByRole("option", { name: "m2" });
    expect(dialog().queryByRole("option", { name: "m1" })).toBeNull(); // current member
    fireEvent.click(dialog().getByRole("button", { name: "Move" })); // nothing chosen: ignored
    expect(move).not.toHaveBeenCalled();
    fireEvent.change(dialog().getByLabelText("To member"), { target: { value: "m2" } });
    fireEvent.click(dialog().getByLabelText("Live migration"));
    expect(dialog().queryByText(/or use live migration/)).toBeNull();
    fireEvent.click(dialog().getByRole("button", { name: "Move" }));
    expect(move).toHaveBeenCalledWith("default", "c1", { target: "m2", pool: undefined, live: true });
  });

  it("moves a stopped container to another pool", async () => {
    const move = vi.spyOn(api, "moveInstance").mockResolvedValue(ok);
    open(full({ status: "Stopped" }));
    fireEvent.click(await screen.findByRole("button", { name: "Move" }));
    expect(dialog().queryByLabelText("Live migration")).toBeNull();
    await dialog().findByRole("option", { name: "remote (ceph)" });
    fireEvent.change(dialog().getByLabelText("To storage pool"), { target: { value: "remote" } });
    fireEvent.click(dialog().getByRole("button", { name: "Move" }));
    expect(move).toHaveBeenCalledWith("default", "c1", { target: undefined, pool: "remote", live: false });
  });

  it("publishes with or without an alias", async () => {
    const pub = vi.spyOn(api, "publishInstance").mockResolvedValue(ok);
    open(full({ status: "Stopped" }));
    fireEvent.click(await screen.findByRole("button", { name: "Publish image" }));
    fireEvent.click(dialog().getByRole("button", { name: "OK" }));
    expect(pub).toHaveBeenLastCalledWith("default", "c1", {});
    fireEvent.click(screen.getByRole("button", { name: "Publish image" }));
    fireEvent.change(dialog().getByLabelText("Alias (optional)"), { target: { value: "golden" } });
    fireEvent.click(dialog().getByRole("button", { name: "OK" }));
    expect(pub).toHaveBeenLastCalledWith("default", "c1", { alias: "golden" });
  });

  it("frozen and stopped instances offer the right buttons; cancel closes dialogs", async () => {
    const state = vi.spyOn(api, "changeState").mockResolvedValue(ok);
    open(full({ status: "Frozen", location: "none" }));
    const bar = within(await screen.findByRole("toolbar", { name: "Instance actions" }));
    expect(screen.getByRole("heading", { level: 1 })).not.toHaveTextContent(/· on /);
    fireEvent.click(bar.getByRole("button", { name: "Unfreeze" }));
    expect(state).toHaveBeenCalledWith("default", "c1", "unfreeze");
    expect(bar.queryByRole("button", { name: "Force stop" })).toBeNull();
    for (const name of ["Rename", "Copy", "Move", "Publish image", "Delete"]) {
      fireEvent.click(bar.getByRole("button", { name }));
      fireEvent.click(dialog().getByRole("button", { name: "Cancel" }));
      expect(screen.queryByRole("dialog")).toBeNull();
    }
  });

  it("viewers get no actions; errors loading are shown", async () => {
    open(full(), { s: viewer() });
    await screen.findByText(/container · Running/);
    expect(screen.queryByRole("toolbar")).toBeNull();
  });

  it("shows load errors", async () => {
    vi.spyOn(api, "fetchInstance").mockRejectedValue(new Error("not found"));
    renderAt(<InstancePage />, { url: "/instances/default/x", path: "/instances/:project/:name" });
    expect(await screen.findByRole("alert")).toHaveTextContent("not found");
  });
});

describe("configuration tab", () => {
  it("edits fields and YAML, then saves the whole instance", async () => {
    const upd = vi.spyOn(api, "updateInstance").mockResolvedValue(ok);
    open(full({ architecture: "x86_64" }), { tab: "config" });
    const save = await screen.findByRole("button", { name: "Save" });
    expect(save).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "web server" } });
    fireEvent.change(screen.getByLabelText(/Profiles/), { target: { value: "default, , big" } });
    fireEvent.change(screen.getByLabelText(/CPU limit/), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText(/Memory limit/), { target: { value: "1GiB" } });
    fireEvent.click(screen.getByLabelText(/Allow nesting/));
    const devices = screen.getByLabelText("Instance devices (YAML)");
    fireEvent.change(devices, { target: { value: "root:\n  type: disk\n  path: /\n  pool: remote\n  size: 10GiB\n" } });
    expect((screen.getByLabelText("Instance config (YAML)") as HTMLTextAreaElement).value).toContain("limits.memory: 1GiB");
    fireEvent.click(save);
    expect(upd).toHaveBeenCalledWith("default", "c1", {
      description: "web server",
      config: {
        "volatile.base_image": "abc",
        "snapshots.schedule": "@daily",
        "limits.memory": "1GiB",
        "security.nesting": "true",
      },
      devices: { root: { type: "disk", path: "/", pool: "remote", size: "10GiB" } },
      profiles: ["default", "big"],
      ephemeral: false,
      stateful: false,
      architecture: "x86_64",
    });
  });

  it("blocks saving invalid YAML and can discard changes", async () => {
    open(full({ devices: undefined as never, profiles: undefined as never, description: undefined as never }), {
      tab: "config",
    });
    const save = await screen.findByRole("button", { name: "Save" });
    fireEvent.change(screen.getByLabelText("Instance config (YAML)"), { target: { value: "a: {b: 1}" } });
    expect(screen.getByRole("alert")).toHaveTextContent("a: values must be");
    fireEvent.change(screen.getByLabelText("Instance config (YAML)"), { target: { value: "a: 1" } });
    expect(screen.queryByRole("alert")).toBeNull();
    fireEvent.change(screen.getByLabelText("Instance devices (YAML)"), { target: { value: "eth0: {name: x}" } });
    expect(screen.getByRole("alert")).toHaveTextContent("every device needs a type");
    expect(save).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Instance devices (YAML)"), { target: { value: "{}" } });
    fireEvent.click(screen.getByLabelText(/Allow nesting/));
    fireEvent.click(screen.getByLabelText(/Allow nesting/)); // on and off again removes the key
    fireEvent.click(screen.getByRole("button", { name: "Discard changes" }));
    expect((screen.getByLabelText("Instance config (YAML)") as HTMLTextAreaElement).value).toContain("limits.cpu");
  });

  it("is read-only for viewers", async () => {
    open(full(), { tab: "config", s: viewer() });
    expect(await screen.findByLabelText("Description")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
    expect(screen.getByLabelText("Instance config (YAML)")).toHaveAttribute("readonly");
  });
});

describe("snapshots tab", () => {
  it("lists newest first and manages snapshots", async () => {
    const restore = vi.spyOn(api, "restoreSnapshot").mockResolvedValue(ok);
    const del = vi.spyOn(api, "deleteSnapshot").mockResolvedValue(ok);
    const ren = vi.spyOn(api, "renameSnapshot").mockResolvedValue(ok);
    const copy = vi.spyOn(api, "copyInstance").mockResolvedValue(ok);
    const pub = vi.spyOn(api, "publishInstance").mockResolvedValue(ok);
    open(full(), { tab: "snapshots" });
    const rows = within(await screen.findByRole("region", { name: "Snapshots" })).getAllByRole("row");
    expect(rows[1]).toHaveTextContent("s2");
    expect(rows[1]).toHaveTextContent("yes");
    expect(rows[2]).toHaveTextContent("never");
    const s1 = within(rows[2]);

    fireEvent.click(s1.getByRole("button", { name: "Restore" }));
    fireEvent.click(dialog().getByRole("button", { name: "Restore" }));
    expect(restore).toHaveBeenCalledWith("default", "c1", "s1", false);
    fireEvent.click(s1.getByRole("button", { name: "Delete" }));
    fireEvent.click(dialog().getByRole("button", { name: "Delete" }));
    expect(del).toHaveBeenCalledWith("default", "c1", "s1");
    fireEvent.click(s1.getByRole("button", { name: "Rename" }));
    fireEvent.change(dialog().getByLabelText("New name"), { target: { value: "base" } });
    fireEvent.click(dialog().getByRole("button", { name: "OK" }));
    expect(ren).toHaveBeenCalledWith("default", "c1", "s1", "base");
    fireEvent.click(s1.getByRole("button", { name: "New instance" }));
    expect((dialog().getByLabelText("Instance name") as HTMLInputElement).value).toBe("c1-s1");
    fireEvent.click(dialog().getByRole("button", { name: "OK" }));
    expect(copy).toHaveBeenCalledWith("default", "c1", { name: "c1-s1", snapshot: "s1" });
    fireEvent.click(s1.getByRole("button", { name: "Publish" }));
    fireEvent.click(dialog().getByRole("button", { name: "OK" }));
    expect(pub).toHaveBeenLastCalledWith("default", "c1", { snapshot: "s1" });
    fireEvent.click(s1.getByRole("button", { name: "Publish" }));
    fireEvent.change(dialog().getByLabelText("Alias (optional)"), { target: { value: "a" } });
    fireEvent.click(dialog().getByRole("button", { name: "OK" }));
    expect(pub).toHaveBeenLastCalledWith("default", "c1", { snapshot: "s1", alias: "a" });
    for (const name of ["Restore", "Delete", "Rename", "New instance", "Publish"]) {
      fireEvent.click(s1.getByRole("button", { name }));
      fireEvent.click(dialog().getByRole("button", { name: "Cancel" }));
    }
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("LXD 6.6+: snapshots and restores the attached volumes too, when asked", async () => {
    vi.spyOn(api, "fetchServer").mockResolvedValue({
      version: "6.10.1", clustered: true, server_name: "m1", auth: "trusted", auth_method: "tls", endpoint: "", features: { snapshot_volumes: true },
    });
    const take = vi.spyOn(api, "createSnapshot").mockResolvedValue(ok);
    const restore = vi.spyOn(api, "restoreSnapshot").mockResolvedValue(ok);
    open(full(), { tab: "snapshots" });
    const box = await screen.findByLabelText(/Include volumes attached only to this instance/);
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "multi" } });
    fireEvent.click(box);
    fireEvent.click(screen.getByRole("button", { name: "Take snapshot" }));
    expect(take).toHaveBeenCalledWith("default", "c1", { name: "multi", stateful: false, disk_volumes_mode: "all-exclusive" });
    const s1 = within(within(screen.getByRole("region", { name: "Snapshots" })).getAllByRole("row")[2]);
    fireEvent.click(s1.getByRole("button", { name: "Restore" }));
    fireEvent.click(dialog().getByLabelText(/Also restore the volumes/));
    fireEvent.click(dialog().getByRole("button", { name: "Restore" }));
    expect(restore).toHaveBeenCalledWith("default", "c1", "s1", true);
    fireEvent.click(s1.getByRole("button", { name: "Delete" }));
    expect(dialog().getByText(/Volume snapshots taken with it are kept/)).toBeInTheDocument();
  });

  it("takes snapshots and saves the schedule", async () => {
    const take = vi.spyOn(api, "createSnapshot").mockResolvedValue(ok);
    const patch = vi.spyOn(api, "patchInstanceConfig").mockResolvedValue(ok);
    open(full({ snapshots: null }), { tab: "snapshots" });
    expect(await screen.findByText("No snapshots.")).toBeInTheDocument();
    const name = screen.getByLabelText("Name") as HTMLInputElement;
    expect(name.value).toMatch(/^snap-\d{8}-\d{4}$/);
    fireEvent.change(name, { target: { value: "before-upgrade" } });
    fireEvent.change(screen.getByLabelText("Expires (optional)"), { target: { value: "2026-12-01T10:00" } });
    fireEvent.click(screen.getByLabelText(/Include memory/));
    fireEvent.click(screen.getByRole("button", { name: "Take snapshot" }));
    expect(take).toHaveBeenCalledWith("default", "c1", {
      name: "before-upgrade",
      stateful: true,
      expires_at: new Date("2026-12-01T10:00").toISOString(),
    });
    fireEvent.change(name, { target: { value: "plain" } });
    fireEvent.change(screen.getByLabelText("Expires (optional)"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Take snapshot" }));
    expect(take).toHaveBeenLastCalledWith("default", "c1", { name: "plain", stateful: true });
    fireEvent.change(name, { target: { value: "  " } });
    expect(screen.getByRole("button", { name: "Take snapshot" })).toBeDisabled();

    expect((screen.getByLabelText(/Schedule/) as HTMLInputElement).value).toBe("@daily");
    fireEvent.change(screen.getByLabelText(/Expire after/), { target: { value: "7d" } });
    fireEvent.click(screen.getByRole("button", { name: "Save schedule" }));
    expect(patch).toHaveBeenCalledWith("default", "c1", {
      "snapshots.schedule": "@daily",
      "snapshots.expiry": "7d",
      "snapshots.pattern": "",
    });
  });

  it("is read-only for viewers", async () => {
    open(full(), { tab: "snapshots", s: viewer() });
    await screen.findByText("s1");
    expect(screen.queryByRole("button", { name: "Restore" })).toBeNull();
    expect(screen.queryByText("Take a snapshot")).toBeNull();
  });
});

describe("logs tab", () => {
  it("shows the first log and switches files", async () => {
    vi.spyOn(api, "fetchLogs").mockResolvedValue(["a.log", "lxc.log"]);
    const log = vi.spyOn(api, "fetchLog").mockImplementation((_p, _n, f) => Promise.resolve(`content of ${f}`));
    open(full(), { tab: "logs" });
    expect(await screen.findByText("content of a.log")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Log file/), { target: { value: "lxc.log" } });
    expect(await screen.findByText("content of lxc.log")).toBeInTheDocument();
    expect(log).toHaveBeenCalledWith("default", "c1", "lxc.log");
  });

  it("handles no logs and errors", async () => {
    vi.spyOn(api, "fetchLogs").mockResolvedValue([]);
    const a = open(full(), { tab: "logs" });
    expect(await screen.findByText("No log files.")).toBeInTheDocument();
    a.unmount();
    vi.spyOn(api, "fetchLogs").mockResolvedValue(["x.log"]);
    vi.spyOn(api, "fetchLog").mockRejectedValue(new Error("gone"));
    const b = open(full(), { tab: "logs" });
    expect(await screen.findByRole("alert")).toHaveTextContent("gone");
    b.unmount();
    vi.spyOn(api, "fetchLogs").mockRejectedValue(new Error("denied"));
    open(full(), { tab: "logs" });
    expect(await screen.findByRole("alert")).toHaveTextContent("denied");
  });

  it("switches tabs from the tab bar", async () => {
    vi.spyOn(api, "fetchLogs").mockResolvedValue([]);
    open();
    fireEvent.click(await screen.findByRole("button", { name: "Logs" }));
    expect(await screen.findByText("No log files.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Logs" })).toHaveAttribute("aria-pressed", "true");
  });
});

const list: api.Instance[] = [
  { project: "default", name: "a", type: "container", status: "Running", location: "m1", description: "web", profiles: ["default"], nics: {} },
  { project: "default", name: "b", type: "virtual-machine", status: "Stopped", location: "m2", description: "", profiles: ["default", "big"], nics: {} },
  { project: "mcui-test", name: "t", type: "container", status: "Running", location: "m1", description: "", nics: {} },
];

describe("instances list", () => {
  it("filters by status, type, member, profile and text", async () => {
    vi.spyOn(api, "fetchInstances").mockResolvedValue(list);
    renderAt(<InstancesPage />);
    await screen.findByRole("link", { name: "a" });
    const names = () =>
      screen
        .getAllByRole("row")
        .slice(1)
        .map((r) => within(r).getAllByRole("link")[0].textContent);
    expect(names()).toEqual(["a", "b", "mcui-test/t"]);
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "Running" } });
    expect(names()).toEqual(["a", "mcui-test/t"]);
    fireEvent.change(screen.getByLabelText("Member"), { target: { value: "m1" } });
    fireEvent.change(screen.getByLabelText("Type"), { target: { value: "container" } });
    fireEvent.change(screen.getByLabelText("Filter instances"), { target: { value: "WEB" } });
    expect(names()).toEqual(["a"]);
    fireEvent.change(screen.getByLabelText("Filter instances"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Member"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Type"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Profile"), { target: { value: "big" } });
    expect(names()).toEqual(["b"]);
  });

  it("follows the project switcher", async () => {
    localStorage.setItem("mcui.project", "mcui-test");
    vi.spyOn(api, "fetchInstances").mockResolvedValue(list);
    renderAt(<InstancesPage />);
    expect(await screen.findByRole("heading", { name: "Instances in mcui-test" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "mcui-test/t" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "a" })).toBeNull();
    localStorage.removeItem("mcui.project");
  });

  it("runs bulk actions on the selection", async () => {
    vi.spyOn(api, "fetchInstances").mockResolvedValue(list);
    const state = vi.spyOn(api, "changeState").mockResolvedValue(ok);
    const del = vi.spyOn(api, "deleteInstance").mockResolvedValue(ok);
    renderAt(<InstancesPage />);
    await screen.findByRole("link", { name: "a" });
    const bar = within(screen.getByRole("toolbar", { name: "Bulk actions" }));
    expect(bar.getByRole("button", { name: "Stop" })).toBeDisabled();
    fireEvent.click(screen.getByLabelText("Select a"));
    fireEvent.click(screen.getByLabelText("Select b"));
    fireEvent.click(screen.getByLabelText("Select b")); // toggle off again
    expect(bar.getByText("1 selected")).toBeInTheDocument();
    fireEvent.click(bar.getByRole("button", { name: "Stop" }));
    expect(state).toHaveBeenCalledWith("default", "a", "stop");
    expect(bar.getByText("0 selected")).toBeInTheDocument();
    expect(await screen.findByText("✓ Stop a")).toBeInTheDocument(); // one instance: named

    fireEvent.click(screen.getByLabelText("Select all"));
    expect(bar.getByText("3 selected")).toBeInTheDocument();
    fireEvent.click(bar.getByRole("button", { name: "Delete" }));
    expect(dialog().getByText("a, b, t")).toBeInTheDocument();
    fireEvent.click(dialog().getByRole("button", { name: "Cancel" }));
    fireEvent.click(bar.getByRole("button", { name: "Delete" }));
    fireEvent.click(dialog().getByRole("button", { name: "Delete" }));
    expect(del.mock.calls).toEqual([
      ["default", "a", true],
      ["default", "b", false],
      ["mcui-test", "t", true],
    ]);
    // one toast for the three, not one each
    expect(await screen.findByText("✓ Delete 3 instances")).toBeInTheDocument();
    expect(screen.queryByText(/Delete a\b/)).toBeNull();
    fireEvent.click(screen.getByLabelText("Select a"));
    fireEvent.click(bar.getByRole("button", { name: "Delete" }));
    expect(screen.getByRole("dialog", { name: "Delete 1 instance?" })).toBeInTheDocument();
    fireEvent.click(dialog().getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByLabelText("Select all"));
    fireEvent.click(screen.getByLabelText("Select all"));
    expect(bar.getByText("0 selected")).toBeInTheDocument();
  });

  it("has no selection or bulk bar for viewers", async () => {
    vi.spyOn(api, "fetchInstances").mockResolvedValue(list);
    renderAt(<InstancesPage />, { s: viewer() });
    await screen.findByRole("link", { name: "a" });
    expect(screen.queryByRole("toolbar")).toBeNull();
    expect(screen.queryByLabelText("Select all")).toBeNull();
  });
});
