import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { renderAt, session } from "../../test/render";
import VolumePage from "./VolumePage";
import VolumesPage from "./VolumesPage";

const ok: api.Started = { operation: null, metadata: null };
const viewer = () => session({ user: { username: "v", role: "viewer" } });
const dialog = () => within(screen.getByRole("dialog"));
const member = (name: string): api.Member => ({ name, url: "", status: "Online", roles: [], failure_domain: "", message: "", architecture: "" });

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.removeItem("mcui.project");
});

function routes(url: string, s = session()) {
  return renderAt(
    <Routes>
      <Route path="/storage" element={<p>storage page</p>} />
      <Route path="/storage/volumes" element={<VolumesPage />} />
      <Route path="/storage/volumes/:project/:pool/:name" element={<VolumePage />} />
    </Routes>,
    { url, s },
  );
}

const volume = (over: Partial<api.Volume> = {}): api.Volume => ({
  name: "data",
  type: "custom",
  content_type: "filesystem",
  description: "app data",
  config: { size: "1GiB" },
  location: "m1",
  used_by: ["/1.0/instances/web"],
  pool: "local",
  ...over,
});

describe("VolumesPage", () => {
  beforeEach(() => {
    vi.spyOn(api, "fetchStoragePools").mockResolvedValue([
      { name: "local", driver: "zfs", status: "Created", description: "" },
      { name: "remote", driver: "ceph", status: "Created", description: "" },
      { name: "fs", driver: "cephfs", status: "Created", description: "" },
      { name: "obj", driver: "cephobject", status: "Created", description: "" },
    ]);
    vi.spyOn(api, "fetchMembers").mockResolvedValue([member("m1"), member("m2")]);
  });

  it("lists volumes, filters by pool, creates on a local pool's member", async () => {
    localStorage.setItem("mcui.project", "lab");
    vi.spyOn(api, "fetchVolumes").mockResolvedValue([
      volume(),
      volume({ name: "blk", pool: "remote", content_type: "block", config: {}, location: "none", used_by: null, description: "" }),
    ]);
    vi.spyOn(api, "fetchVolume").mockReturnValue(new Promise(() => {}));
    vi.spyOn(api, "fetchVolumeSnapshots").mockReturnValue(new Promise(() => {}));
    const create = vi.spyOn(api, "createVolume").mockResolvedValue(ok);
    routes("/storage/volumes");
    const row = (await screen.findByRole("link", { name: "data" })).closest("tr")!;
    expect(row).toHaveTextContent("localfilesystem1GiBm11app data");
    expect(screen.getByRole("link", { name: "data" })).toHaveAttribute("href", "/storage/volumes/lab/local/data?member=m1");
    expect(screen.getByRole("link", { name: "blk" }).closest("tr")).toHaveTextContent("remoteblock––0");
    fireEvent.change(screen.getByLabelText("Pool"), { target: { value: "remote" } });
    expect(screen.queryByRole("link", { name: "data" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Create volume" }));
    fireEvent.click(dialog().getByRole("button", { name: "Create" }));
    expect(dialog().getByRole("alert")).toHaveTextContent("Name:");
    fireEvent.change(dialog().getByLabelText("Name"), { target: { value: " new " } });
    const pool = await dialog().findByRole("option", { name: "local (zfs)" });
    expect(dialog().queryByRole("option", { name: /obj/ })).toBeNull();
    expect(pool).toBeInTheDocument();
    fireEvent.change(await dialog().findByLabelText("Member"), { target: { value: "m2" } });
    fireEvent.click(dialog().getByLabelText(/Block/));
    fireEvent.change(dialog().getByLabelText("Size"), { target: { value: "2GiB" } });
    fireEvent.change(dialog().getByLabelText("Description"), { target: { value: "x" } });
    fireEvent.click(dialog().getByRole("button", { name: "Create" }));
    expect(create).toHaveBeenCalledWith({
      name: "new",
      pool: "local",
      project: "lab",
      content_type: "block",
      description: "x",
      config: { size: "2GiB" },
      target: "m2",
    });
    expect(await screen.findByRole("heading", { name: /Volume new/ })).toBeInTheDocument();
  });

  it("CephFS volumes are filesystems; Ceph RBD needs no member", async () => {
    vi.spyOn(api, "fetchVolumes").mockResolvedValue([]);
    const create = vi.spyOn(api, "createVolume").mockResolvedValue(ok);
    vi.spyOn(api, "fetchVolume").mockReturnValue(new Promise(() => {}));
    vi.spyOn(api, "fetchVolumeSnapshots").mockReturnValue(new Promise(() => {}));
    routes("/storage/volumes");
    expect(await screen.findByText("No custom volumes.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Create volume" }));
    fireEvent.change(dialog().getByLabelText("Name"), { target: { value: "shared" } });
    await dialog().findByRole("option", { name: "fs (cephfs)" });
    fireEvent.change(dialog().getByLabelText("Pool"), { target: { value: "fs" } });
    expect(dialog().queryByLabelText(/Block/)).toBeNull();
    expect(dialog().queryByLabelText("Member")).toBeNull();
    fireEvent.click(dialog().getByRole("button", { name: "Create" }));
    expect(create).toHaveBeenCalledWith({ name: "shared", pool: "fs", project: "default", content_type: "filesystem", description: "", config: {} });
  });

  it("viewer, cancel, no pools and error states", async () => {
    vi.spyOn(api, "fetchVolumes").mockResolvedValue([volume()]);
    const a = routes("/storage/volumes", viewer());
    expect(await screen.findByRole("link", { name: "data" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create volume" })).toBeNull();
    expect(screen.queryByLabelText("Pool")).toBeNull();
    a.unmount();
    vi.spyOn(api, "fetchStoragePools").mockResolvedValue([]);
    const b = routes("/storage/volumes");
    fireEvent.click(await screen.findByRole("button", { name: "Create volume" }));
    fireEvent.change(dialog().getByLabelText("Name"), { target: { value: "x" } });
    fireEvent.click(dialog().getByRole("button", { name: "Create" }));
    expect(dialog().getByRole("alert")).toHaveTextContent("Choose a pool");
    fireEvent.click(dialog().getByRole("button", { name: "Cancel" }));
    b.unmount();
    vi.spyOn(api, "fetchVolumes").mockRejectedValue(new Error("down"));
    routes("/storage/volumes");
    expect(await screen.findByRole("alert")).toHaveTextContent("down");
  });
});

const detail = (over: Partial<api.VolumeDetail> = {}): api.VolumeDetail => ({
  ...volume(),
  config: { size: "1GiB", "snapshots.schedule": "@daily", "zfs.use_refquota": "true" },
  used_by: ["/1.0/instances/web", "/1.0/instances/db?project=lab", "/1.0/profiles/default"],
  created_at: "2026-11-01T10:00:00Z",
  state: { usage: { used: 512 * 1024 ** 2 } },
  ...over,
});

const snap = (name: string, expires: string | null): api.VolumeSnapshot => ({
  name: `data/${name}`,
  description: "",
  created_at: "2026-11-02T10:00:00Z",
  expires_at: expires,
  config: {},
});

describe("VolumePage", () => {
  beforeEach(() => {
    vi.spyOn(api, "fetchInstance").mockImplementation((_p, n) =>
      Promise.resolve({
        devices: n === "web" ? { data: { type: "disk", pool: "local", source: "data", path: "/srv" }, eth0: { type: "nic" } } : {},
      } as unknown as api.InstanceFull),
    );
  });

  it("edits settings, snapshots and attachments", async () => {
    vi.spyOn(api, "fetchVolume").mockResolvedValue(detail());
    vi.spyOn(api, "fetchVolumeSnapshots").mockResolvedValue([snap("s0", "0001-01-01T00:00:00Z"), snap("s1", "2026-12-01T00:00:00Z")]);
    vi.spyOn(api, "fetchInstances").mockResolvedValue([
      { project: "default", name: "web", type: "container", status: "Running", location: "m1", description: "", nics: {} },
      { project: "default", name: "vm1", type: "virtual-machine", status: "Stopped", location: "m1", description: "", nics: {} },
      { project: "lab", name: "other", type: "container", status: "Running", location: "m1", description: "", nics: {} },
    ]);
    const put = vi.spyOn(api, "updateVolume").mockResolvedValue(ok);
    const snapCreate = vi.spyOn(api, "createVolumeSnapshot").mockResolvedValue(ok);
    const snapRename = vi.spyOn(api, "renameVolumeSnapshot").mockResolvedValue(ok);
    const snapRestore = vi.spyOn(api, "restoreVolumeSnapshot").mockResolvedValue(ok);
    const snapDelete = vi.spyOn(api, "deleteVolumeSnapshot").mockResolvedValue(ok);
    const attach = vi.spyOn(api, "attachVolume").mockResolvedValue(ok);
    const detach = vi.spyOn(api, "detachVolume").mockResolvedValue(ok);
    routes("/storage/volumes/default/local/data?member=m1");
    expect(await screen.findByText("512 MiB of 1GiB")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "local" })).toHaveAttribute("href", "/storage/pools/local");
    expect(screen.getByRole("link", { name: "m1" })).toBeInTheDocument();

    // settings: form keys and the rest as YAML
    expect(screen.getByLabelText("Snapshot schedule (cron or @daily)")).toHaveValue("@daily");
    expect(screen.getByLabelText("Other config (YAML)")).toHaveValue("zfs.use_refquota: \"true\"\n");
    fireEvent.change(screen.getByLabelText("Size (raise to grow it)"), { target: { value: "2GiB" } });
    fireEvent.change(screen.getByLabelText("Snapshot schedule (cron or @daily)"), { target: { value: " " } });
    fireEvent.change(screen.getByLabelText("Snapshots expire after"), { target: { value: "2w" } });
    fireEvent.change(screen.getByLabelText("Other config (YAML)"), { target: { value: "- x" } });
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Other config (YAML)"), { target: { value: "user.note: hi" } });
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "edited" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(put).toHaveBeenCalledWith(
      "default",
      "local",
      "data",
      { description: "edited", config: { "user.note": "hi", size: "2GiB", "snapshots.expiry": "2w" } },
      "m1",
    );

    // used by: own device can be detached; db gets it through a profile
    expect(await screen.findByText(/as data/)).toBeInTheDocument();
    expect(await screen.findByText("(through a profile)")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "profile default" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Detach" }));
    expect(detach).toHaveBeenCalledWith("default", "local", "data", { instance: "web", device: "data" });

    // attach
    fireEvent.click(screen.getByRole("button", { name: "Attach…" }));
    expect(await dialog().findByRole("option", { name: "web (container, Running)" })).toBeInTheDocument();
    expect(dialog().queryByRole("option", { name: /other/ })).toBeNull();
    fireEvent.change(dialog().getByLabelText("Mount path"), { target: { value: "relative" } });
    fireEvent.click(dialog().getByRole("button", { name: "Attach" }));
    expect(dialog().getByRole("alert")).toHaveTextContent("absolute");
    fireEvent.change(dialog().getByLabelText("Device name"), { target: { value: " " } });
    fireEvent.click(dialog().getByRole("button", { name: "Attach" }));
    expect(dialog().getByRole("alert")).toHaveTextContent("device name");
    fireEvent.change(dialog().getByLabelText("Device name"), { target: { value: "d2" } });
    fireEvent.change(dialog().getByLabelText("Mount path"), { target: { value: "/mnt/d2" } });
    fireEvent.change(dialog().getByLabelText("Instance"), { target: { value: "vm1" } });
    fireEvent.click(dialog().getByRole("button", { name: "Attach" }));
    expect(attach).toHaveBeenCalledWith("default", "local", "data", { instance: "vm1", device: "d2", path: "/mnt/d2" });

    // snapshots
    const s0 = screen.getByText("s0").closest("tr")!;
    expect(s0).toHaveTextContent("never");
    expect(screen.getByText("s1").closest("tr")).not.toHaveTextContent("never");
    fireEvent.click(screen.getByRole("button", { name: "Create snapshot" }));
    expect(dialog().getByLabelText("Name")).toHaveValue("snap2");
    fireEvent.change(dialog().getByLabelText("Expires (optional)"), { target: { value: "2030-01-01T10:00" } });
    fireEvent.click(dialog().getByRole("button", { name: "Create" }));
    expect(snapCreate).toHaveBeenCalledWith("default", "local", "data", { name: "snap2", expires_at: new Date("2030-01-01T10:00").toISOString() }, "m1");
    fireEvent.click(screen.getByRole("button", { name: "Create snapshot" }));
    fireEvent.change(dialog().getByLabelText("Name"), { target: { value: " " } });
    fireEvent.click(dialog().getByRole("button", { name: "Create" }));
    fireEvent.change(dialog().getByLabelText("Name"), { target: { value: "plain" } });
    fireEvent.click(dialog().getByRole("button", { name: "Create" }));
    expect(snapCreate).toHaveBeenLastCalledWith("default", "local", "data", { name: "plain" }, "m1");
    fireEvent.click(within(s0).getByRole("button", { name: "Rename" }));
    fireEvent.change(dialog().getByLabelText("New name"), { target: { value: "first" } });
    fireEvent.click(dialog().getByRole("button", { name: "OK" }));
    expect(snapRename).toHaveBeenCalledWith("default", "local", "data", "s0", "first", "m1");
    fireEvent.click(within(s0).getByRole("button", { name: "Restore" }));
    fireEvent.click(dialog().getByRole("button", { name: "Restore" }));
    expect(snapRestore).toHaveBeenCalledWith("default", "local", "data", "s0", "m1");
    fireEvent.click(within(s0).getByRole("button", { name: "Delete" }));
    fireEvent.click(dialog().getByRole("button", { name: "Delete" }));
    expect(snapDelete).toHaveBeenCalledWith("default", "local", "data", "s0", "m1");
  });

  it("renames and deletes; block volumes attach without a path", async () => {
    vi.spyOn(api, "fetchVolume").mockResolvedValue(detail({ content_type: "block", used_by: [], config: {}, state: null, created_at: undefined }));
    vi.spyOn(api, "fetchVolumeSnapshots").mockResolvedValue([]);
    vi.spyOn(api, "fetchInstances").mockResolvedValue([
      { project: "default", name: "vm1", type: "virtual-machine", status: "Stopped", location: "m1", description: "", nics: {} },
    ]);
    vi.spyOn(api, "fetchVolumes").mockResolvedValue([]);
    const ren = vi.spyOn(api, "renameVolume").mockResolvedValue(ok);
    const del = vi.spyOn(api, "deleteVolume").mockResolvedValue(ok);
    const attach = vi.spyOn(api, "attachVolume").mockResolvedValue(ok);
    routes("/storage/volumes/default/remote/data");
    expect(await screen.findByText("Not attached to anything.")).toBeInTheDocument();
    expect(screen.getByText("None.")).toBeInTheDocument();
    expect(screen.queryByText("Created")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Attach…" }));
    expect(dialog().getByText(/without a mount path/)).toBeInTheDocument();
    await dialog().findByRole("option", { name: /vm1/ });
    fireEvent.click(dialog().getByRole("button", { name: "Attach" }));
    expect(attach).toHaveBeenCalledWith("default", "remote", "data", { instance: "vm1", device: "data" });
    fireEvent.click(screen.getByRole("button", { name: "Rename" }));
    fireEvent.change(dialog().getByLabelText("New name"), { target: { value: "data2" } });
    fireEvent.click(dialog().getByRole("button", { name: "OK" }));
    expect(ren).toHaveBeenCalledWith("default", "remote", "data", "data2", undefined);
    await screen.findByRole("heading", { name: /Volume data2/ });
    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
    fireEvent.click(dialog().getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    fireEvent.click(dialog().getByRole("button", { name: "Delete" }));
    expect(del).toHaveBeenCalledWith("default", "remote", "data2", undefined);
    expect(await screen.findByRole("heading", { name: "Custom volumes in default" })).toBeInTheDocument();
  });

  it("attach needs an instance; viewers read only; errors show", async () => {
    vi.spyOn(api, "fetchVolume").mockResolvedValue(detail());
    vi.spyOn(api, "fetchVolumeSnapshots").mockRejectedValue(new Error("snapshots down"));
    vi.spyOn(api, "fetchInstances").mockResolvedValue([]);
    const a = routes("/storage/volumes/default/local/data");
    fireEvent.click(await screen.findByRole("button", { name: "Attach…" }));
    fireEvent.click(dialog().getByRole("button", { name: "Attach" }));
    expect(dialog().getByRole("alert")).toHaveTextContent("Choose an instance");
    fireEvent.click(dialog().getByRole("button", { name: "Cancel" }));
    expect(await screen.findByText("snapshots down")).toBeInTheDocument();
    a.unmount();
    vi.spyOn(api, "fetchVolumeSnapshots").mockResolvedValue([snap("s0", null)]);
    const b = routes("/storage/volumes/default/local/data", viewer());
    expect(await screen.findByText("s0")).toBeInTheDocument();
    expect(screen.queryByRole("toolbar")).toBeNull();
    expect(screen.queryByRole("button", { name: "Detach" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Restore" })).toBeNull();
    expect(screen.getByLabelText("Description")).toBeDisabled();
    b.unmount();
    vi.spyOn(api, "fetchVolume").mockRejectedValue(new Error("Storage volume not found"));
    routes("/storage/volumes/default/local/gone");
    await waitFor(() => expect(screen.getAllByRole("alert")[0]).toHaveTextContent("Storage volume not found"));
  });
});
