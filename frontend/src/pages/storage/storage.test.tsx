import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { renderAt, session } from "../../test/render";
import StoragePage from "../StoragePage";
import CreatePoolPage from "./CreatePoolPage";
import PoolPage from "./PoolPage";
import { buildPool, emptyPool, poolProblems } from "./request";

const ok: api.Started = { operation: null, metadata: null };
const viewer = () => session({ user: { username: "v", role: "viewer" } });
const dialog = () => within(screen.getByRole("dialog"));
const member = (name: string): api.Member => ({ name, url: "", status: "Online", roles: [], failure_domain: "", message: "", architecture: "" });
const noData: api.QueryResult = { name: "", fn: "value", start: 0, end: 1, resolution: 0, series: [] } as never;

beforeEach(() => {
  vi.spyOn(api, "queryMetric").mockResolvedValue(noData);
  vi.spyOn(api, "fetchServer").mockResolvedValue({
    version: "6.10.1", clustered: true, server_name: "m1", auth: "trusted", auth_method: "tls", endpoint: "", features: { cephfs_path: true },
  });
});
afterEach(() => vi.restoreAllMocks());

function routes(url: string, s = session()) {
  return renderAt(
    <Routes>
      <Route path="/storage" element={<StoragePage />} />
      <Route path="/storage/pools/new" element={<CreatePoolPage />} />
      <Route path="/storage/pools/:name" element={<PoolPage />} />
    </Routes>,
    { url, s },
  );
}

describe("pool request builder", () => {
  it("splits cluster-wide and per-member keys for local drivers", () => {
    const f = {
      ...emptyPool(),
      name: " fast ",
      driver: "zfs" as const,
      description: " nvme ",
      perMember: { m1: { source: " /dev/sdb ", size: "" }, m2: { source: "", size: "20GiB" } },
      shared: { "zfs.pool_name": " tank " },
      extra: { "volume.zfs.remove_snapshots": "true" },
    };
    expect(poolProblems(f)).toEqual([]);
    expect(buildPool(f, ["m1", "m2", "m3"])).toEqual({
      name: "fast",
      driver: "zfs",
      description: "nvme",
      config: { "volume.zfs.remove_snapshots": "true" },
      members: {
        m1: { source: "/dev/sdb", "zfs.pool_name": "tank" },
        m2: { size: "20GiB", "zfs.pool_name": "tank" },
        m3: { "zfs.pool_name": "tank" },
      },
    });
  });

  it("dir pools take no size; remote drivers have no per-member keys", () => {
    const dir = { ...emptyPool(), name: "d", driver: "dir" as const, perMember: { m1: { source: "/srv/lxd", size: "9GiB" } } };
    expect(buildPool(dir, ["m1", "m2"]).members).toEqual({ m1: { source: "/srv/lxd" } });
    const ceph = { ...emptyPool(), name: "r", driver: "ceph" as const, perMember: { m1: { source: "x", size: "" } }, fields: { "ceph.osd.pool_name": " lxd " } };
    expect(buildPool(ceph, ["m1"])).toEqual({ name: "r", driver: "ceph", description: "", config: { "ceph.osd.pool_name": "lxd" }, members: {} });
  });

  it("CephFS takes cephfs.path on LXD 6.8+ and source before (storage_remote_drop_source)", () => {
    const fs = { ...emptyPool(), name: "f", driver: "cephfs" as const, fields: { source: "lxd_cephfs" } };
    expect(buildPool(fs, ["m1"]).config).toEqual({ source: "lxd_cephfs" });
    expect(buildPool(fs, ["m1"], { cephfs_path: false }).config).toEqual({ source: "lxd_cephfs" });
    expect(buildPool(fs, ["m1"], { cephfs_path: true }).config).toEqual({ "cephfs.path": "lxd_cephfs" });
    // other drivers keep their own keys
    const dir = { ...emptyPool(), name: "d", driver: "dir" as const, perMember: { m1: { source: "/srv", size: "" } } };
    expect(buildPool(dir, ["m1"], { cephfs_path: true }).members).toEqual({ m1: { source: "/srv" } });
  });

  it("explains what's missing", () => {
    expect(poolProblems(emptyPool())).toEqual(["Name: letters, digits, dots, dashes and underscores"]);
    expect(poolProblems({ ...emptyPool(), name: "fs", driver: "cephfs" })).toEqual(["Enter the CephFS filesystem"]);
  });
});

describe("StoragePage pool list", () => {
  it("lists pools; admins can create", async () => {
    vi.spyOn(api, "fetchStoragePools").mockResolvedValue([
      { name: "remote", driver: "ceph", status: "Created", description: "rbd", config: { "ceph.osd.pool_name": "lxd_remote" }, used_by: 7 },
      { name: "local", driver: "zfs", status: "Created", description: "", config: { source: "tank" } },
      { name: "bare", driver: "dir", status: "Pending", description: "" },
    ]);
    routes("/storage");
    const row = (await screen.findByRole("link", { name: "remote" })).closest("tr")!;
    expect(row).toHaveTextContent("lxd_remote");
    expect(row).toHaveTextContent("7");
    expect(screen.getByRole("link", { name: "local" }).closest("tr")).toHaveTextContent("tank");
    expect(screen.getByRole("link", { name: "bare" }).closest("tr")).toHaveTextContent("–");
    expect(screen.getByRole("link", { name: "Create pool" })).toHaveAttribute("href", "/storage/pools/new");
  });

  it("viewer, empty and error states", async () => {
    vi.spyOn(api, "fetchStoragePools").mockResolvedValue([]);
    const a = routes("/storage", viewer());
    expect(await screen.findByText("No storage pools.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Create pool" })).toBeNull();
    a.unmount();
    vi.spyOn(api, "fetchStoragePools").mockRejectedValue(new Error("pools down"));
    routes("/storage");
    expect(await screen.findByText("pools down")).toBeInTheDocument();
  });
});

describe("CreatePoolPage", () => {
  beforeEach(() => {
    vi.spyOn(api, "fetchMembers").mockResolvedValue([member("m1"), member("m2")]);
    vi.spyOn(api, "fetchPool").mockResolvedValue({ name: "p", driver: "zfs", description: "", status: "Created", config: {}, used_by: [], locations: [] });
    vi.spyOn(api, "fetchPoolMembers").mockResolvedValue([]);
  });

  it("creates a ZFS pool with loop files on every member", async () => {
    const create = vi.spyOn(api, "createPool").mockResolvedValue(ok);
    routes("/storage/pools/new");
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Name:");
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "fast" } });
    const size = await screen.findByLabelText("m1: loop file size");
    const copy = screen.getByRole("button", { name: "Use m1's values on every member" });
    expect(copy).toBeDisabled();
    fireEvent.change(size, { target: { value: "5GiB" } });
    fireEvent.change(screen.getByLabelText("m1: source"), { target: { value: "" } });
    fireEvent.click(copy);
    expect(screen.getByLabelText("m2: loop file size")).toHaveValue("5GiB");
    fireEvent.change(screen.getByLabelText("ZFS pool name"), { target: { value: "lxdfast" } });
    fireEvent.change(screen.getByLabelText(/^Other settings/), { target: { value: "- x" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(screen.getByRole("alert")).toHaveTextContent("config must be a mapping");
    fireEvent.change(screen.getByLabelText(/^Other settings/), { target: { value: "volume.size: 5GiB" } });
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "loop" } });
    expect(within(screen.getByLabelText("Request preview")).getByText(/lxdfast/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(create).toHaveBeenCalledWith({
      name: "fast",
      driver: "zfs",
      description: "loop",
      config: { "volume.size": "5GiB" },
      members: { m1: { size: "5GiB", "zfs.pool_name": "lxdfast" }, m2: { size: "5GiB", "zfs.pool_name": "lxdfast" } },
    });
    expect(await screen.findByRole("heading", { name: /Storage pool fast/ })).toBeInTheDocument();
  });

  it("dir pools ask only for a directory; Ceph pools have no per-member part; LXD refusals show", async () => {
    const create = vi.spyOn(api, "createPool").mockRejectedValue(new Error("Ceph pool exists"));
    routes("/storage/pools/new");
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "d" } });
    fireEvent.click(screen.getByRole("radio", { name: /^Directory/ }));
    expect(await screen.findByLabelText("m1: source")).toBeInTheDocument();
    expect(screen.queryByLabelText("m1: loop file size")).toBeNull();
    fireEvent.click(screen.getByRole("radio", { name: /^LVM/ }));
    expect(screen.getByLabelText("Volume group")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Use a thin pool (true/false)"), { target: { value: "false" } });
    fireEvent.click(screen.getByRole("radio", { name: /^Btrfs/ }));
    fireEvent.click(screen.getByRole("radio", { name: /^CephFS/ }));
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Enter the CephFS filesystem");
    fireEvent.click(screen.getByRole("radio", { name: /^Ceph RBD/ }));
    expect(screen.queryByLabelText("m1: source")).toBeNull();
    fireEvent.change(screen.getByLabelText("Ceph OSD pool"), { target: { value: "lxd2" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(create).toHaveBeenCalledWith({ name: "d", driver: "ceph", description: "", config: { "ceph.osd.pool_name": "lxd2" }, members: {} });
    expect(await screen.findByText("Creating the pool failed: Ceph pool exists")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(await screen.findByRole("heading", { name: "Storage" })).toBeInTheDocument();
  });

  it("needs the admin role", () => {
    routes("/storage/pools/new", viewer());
    expect(screen.getByRole("alert")).toHaveTextContent("needs the admin role");
  });
});

const detail = (over: Partial<api.PoolDetail> = {}): api.PoolDetail => ({
  name: "local",
  driver: "zfs",
  description: "nvme",
  status: "Created",
  config: { "volume.zfs.remove_snapshots": "true" },
  used_by: [
    "/1.0/instances/c1",
    "/1.0/profiles/default",
    "/1.0/storage-pools/local/volumes/custom/v1",
    "/1.0/storage-pools/local/volumes/custom/v2?project=p&target=m2",
  ],
  locations: ["m1", "m2"],
  ...over,
});

describe("PoolPage", () => {
  it("shows capacity per member and edits settings", async () => {
    vi.spyOn(api, "fetchPool").mockResolvedValue(detail());
    vi.spyOn(api, "fetchPoolMembers").mockResolvedValue([
      {
        member: "m1",
        config: { source: "tank", "zfs.pool_name": "tank" },
        status: "Created",
        resources: { space: { used: 2 * 1024 ** 3, total: 8 * 1024 ** 3 }, inodes: { used: 10, total: 1000 } },
      },
      { member: "m2", config: {}, error: "Member offline" },
      { member: "m3", config: {}, status: "Created", resources: { space: { used: 0, total: 0 } } },
    ]);
    const put = vi.spyOn(api, "updatePool").mockResolvedValue(ok);
    const del = vi.spyOn(api, "deletePool").mockResolvedValue(ok);
    vi.spyOn(api, "fetchStoragePools").mockResolvedValue([]);
    routes("/storage/pools/local");
    expect(await screen.findByRole("heading", { name: "Storage pool local zfs · Created" })).toBeInTheDocument();
    const m1 = (await screen.findByRole("link", { name: "m1" })).closest("tr")!;
    expect(m1).toHaveTextContent("2.0 GiB");
    expect(m1).toHaveTextContent("8.0 GiB");
    expect(m1).toHaveTextContent("25");
    expect(m1).toHaveTextContent("10 / 1.0k");
    expect(m1).toHaveTextContent("tank");
    expect(within(m1).getByRole("meter", { name: "m1 used" })).toHaveAttribute("value", "25");
    expect(screen.getByRole("link", { name: "m2" }).closest("tr")).toHaveTextContent("Member offline");
    expect(screen.getByRole("link", { name: "m3" }).closest("tr")).toHaveTextContent("Created0 B0 B––");
    expect(screen.getByRole("figure", { name: "local: space used" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "volume local/v1" })).toHaveAttribute("href", "/storage/volumes/default/local/v1");
    expect(screen.getByRole("link", { name: "volume local/v2" })).toHaveAttribute("href", "/storage/volumes/p/local/v2?member=m2");

    fireEvent.change(screen.getByLabelText("m1"), { target: { value: "size: 30GiB" } });
    fireEvent.click(screen.getByRole("button", { name: "Save m1" }));
    expect(put).toHaveBeenCalledWith("local", { config: { size: "30GiB" } }, "m1");
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "edited" } });
    fireEvent.change(screen.getByLabelText("Config (YAML), cluster-wide"), { target: { value: "- bad" } });
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Config (YAML), cluster-wide"), { target: { value: "volume.size: 10GiB" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(put).toHaveBeenLastCalledWith("local", { description: "edited", config: { "volume.size": "10GiB" } });

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    fireEvent.click(dialog().getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    fireEvent.click(dialog().getByRole("button", { name: "Delete" }));
    expect(del).toHaveBeenCalledWith("local");
    expect(await screen.findByRole("heading", { name: "Storage" })).toBeInTheDocument();
  });

  it("Ceph pools: no per-member settings; CephFS reports no used space", async () => {
    vi.spyOn(api, "fetchPool").mockResolvedValue(detail({ name: "remote-fs", driver: "cephfs" }));
    vi.spyOn(api, "fetchPoolMembers").mockResolvedValue([
      { member: "m1", config: { source: "lxd_cephfs" }, status: "Created", resources: { space: { total: 50 * 1024 ** 3 } } },
      { member: "m2", config: { source: "lxd_cephfs" }, status: "Created", resources: { space: { total: 50 * 1024 ** 3 } } },
    ]);
    routes("/storage/pools/remote-fs");
    const row = (await screen.findByRole("link", { name: "m1" })).closest("tr")!;
    expect(row).toHaveTextContent("Created–50.0 GiB––lxd_cephfs");
    expect(screen.queryByRole("heading", { name: "Per-member settings" })).toBeNull();
  });

  it("single member: no per-member section; viewers read only; errors show", async () => {
    vi.spyOn(api, "fetchPool").mockResolvedValue(detail({ used_by: null, locations: null }));
    vi.spyOn(api, "fetchPoolMembers").mockResolvedValue([{ member: "lxd1", config: {}, status: "Created" }]);
    const a = routes("/storage/pools/local", viewer());
    expect(await screen.findByRole("link", { name: "lxd1" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Per-member settings" })).toBeNull();
    expect(screen.queryByRole("toolbar")).toBeNull();
    expect(screen.getByText("Nothing.")).toBeInTheDocument();
    a.unmount();
    vi.spyOn(api, "fetchPoolMembers").mockRejectedValue(new Error("resources down"));
    const b = routes("/storage/pools/local");
    expect(await screen.findByText("resources down")).toBeInTheDocument();
    b.unmount();
    vi.spyOn(api, "fetchPool").mockRejectedValue(new Error("Storage pool not found"));
    routes("/storage/pools/gone");
    await waitFor(() => expect(screen.getAllByRole("alert")[0]).toHaveTextContent("Storage pool not found"));
  });
});
