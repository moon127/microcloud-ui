import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { renderAt, session } from "../../test/render";
import VolumePage from "./VolumePage";
import VolumesPage from "./VolumesPage";

const ok: api.Started = { operation: null, metadata: null };
const dialog = () => within(screen.getByRole("dialog"));
const member = (name: string): api.Member => ({ name, url: "", status: "Online", roles: [], failure_domain: "", message: "", architecture: "" });
const operator = () => session({ user: { username: "o", role: "project", grants: { web: "operator" } } });
const detail: api.VolumeDetail = {
  name: "data",
  type: "custom",
  content_type: "filesystem",
  description: "",
  config: {},
  location: "m1",
  used_by: [],
  pool: "local",
  state: null,
};

function open(url: string, s = session()) {
  return renderAt(
    <Routes>
      <Route path="/storage/volumes" element={<VolumesPage />} />
      <Route path="/storage/volumes/:project/:pool/:name" element={<VolumePage />} />
    </Routes>,
    { url, s },
  );
}

beforeEach(() => {
  vi.spyOn(api, "fetchProject").mockResolvedValue({ name: "web", description: "", config: {}, used_by: [] });
  vi.spyOn(api, "fetchInstances").mockResolvedValue([]);
  vi.spyOn(api, "fetchStoragePools").mockResolvedValue([
    { name: "local", driver: "zfs", status: "Created", description: "" },
    { name: "remote", driver: "ceph", status: "Created", description: "" },
    { name: "obj", driver: "cephobject", status: "Created", description: "" },
  ]);
  vi.spyOn(api, "fetchMembers").mockResolvedValue([member("m1"), member("m2")]);
});
afterEach(() => {
  vi.restoreAllMocks();
  localStorage.removeItem("mcui.project");
});

describe("a volume's backups", () => {
  it("lists and makes them on the volume's member; operators get snapshots and backups", async () => {
    vi.spyOn(api, "fetchVolume").mockResolvedValue(detail);
    vi.spyOn(api, "fetchVolumeSnapshots").mockResolvedValue([
      { name: "data/s1", description: "", created_at: "2026-11-13T10:00:00Z", config: {} },
    ]);
    const list = vi.spyOn(api, "fetchVolumeBackups").mockResolvedValue([
      { name: "b1", created_at: "2026-11-13T10:00:00Z", volume_only: true },
    ]);
    const make = vi.spyOn(api, "createVolumeBackup").mockResolvedValue(ok);
    const ren = vi.spyOn(api, "renameVolumeBackup").mockResolvedValue(ok);
    const del = vi.spyOn(api, "deleteVolumeBackup").mockResolvedValue(ok);
    open("/storage/volumes/web/local/data?member=m1", operator());
    const region = await screen.findByRole("region", { name: "Backups" });
    const row = within((await within(region).findAllByRole("row"))[1]);
    expect(list).toHaveBeenCalledWith("web", "local", "data", "m1");
    expect(row.getByText("left out")).toBeInTheDocument();
    expect(row.getByRole("link", { name: "Download" })).toHaveAttribute(
      "href",
      "/api/volumes/web/local/data/backups/b1/export?target=m1",
    );
    // snapshots: an operator's right, as on the server
    expect(screen.getByRole("button", { name: "Create snapshot" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Attach…" })).toBeNull(); // managing stays with admins
    fireEvent.change(within(region).getByLabelText("Name"), { target: { value: "full" } });
    fireEvent.click(within(region).getByLabelText(/Leave out snapshots/));
    fireEvent.click(within(region).getByRole("button", { name: "Make backup" }));
    expect(make).toHaveBeenCalledWith("web", "local", "data", { name: "full", volume_only: true, optimized_storage: false }, "m1");
    fireEvent.click(row.getByRole("button", { name: "Rename" }));
    fireEvent.change(dialog().getByLabelText("New name"), { target: { value: "b2" } });
    fireEvent.click(dialog().getByRole("button", { name: "OK" }));
    expect(ren).toHaveBeenCalledWith("web", "local", "data", "b1", "b2", "m1");
    fireEvent.click(row.getByRole("button", { name: "Delete" }));
    fireEvent.click(dialog().getByRole("button", { name: "Delete" }));
    expect(del).toHaveBeenCalledWith("web", "local", "data", "b1", "m1");
  });
});

describe("Volumes → Import backup", () => {
  beforeEach(() => vi.spyOn(api, "fetchVolumes").mockResolvedValue([]));

  it("uploads to a local pool's member with progress", async () => {
    localStorage.setItem("mcui.project", "web");
    let report: (f: number) => void = () => {};
    const imp = vi.spyOn(api, "importVolumeBackup").mockImplementation((_f, _d, onProgress) => {
      report = onProgress!;
      return Promise.resolve(ok);
    });
    open("/storage/volumes");
    fireEvent.click(await screen.findByRole("button", { name: "Import backup" }));
    await waitFor(() => expect(dialog().getByLabelText("Member")).toBeInTheDocument());
    expect(within(dialog().getByLabelText("Pool")).queryByText(/obj/)).toBeNull();
    fireEvent.click(dialog().getByRole("button", { name: "Import" }));
    expect(dialog().getByRole("alert")).toHaveTextContent("Choose a backup file.");
    const file = new File(["t"], "data.tar.gz");
    fireEvent.change(dialog().getByLabelText("Backup file"), { target: { files: [file] } });
    fireEvent.change(dialog().getByLabelText(/^Name/), { target: { value: "-bad" } });
    fireEvent.click(dialog().getByRole("button", { name: "Import" }));
    expect(dialog().getByRole("alert")).toHaveTextContent(/Names start/);
    fireEvent.change(dialog().getByLabelText(/^Name/), { target: { value: "restored" } });
    fireEvent.change(dialog().getByLabelText("Member"), { target: { value: "m2" } });
    fireEvent.click(dialog().getByRole("button", { name: "Import" }));
    expect(imp).toHaveBeenCalledWith(file, { project: "web", pool: "local", name: "restored", target: "m2" }, expect.any(Function));
    const toasts = screen.getByRole("list", { name: "Actions" });
    act(() => report(0.5));
    await waitFor(() => expect(toasts).toHaveTextContent("✓ Import data.tar.gz into local"));
  });

  it("a remote pool has no member choice; Cancel closes", async () => {
    vi.spyOn(api, "fetchStoragePools").mockResolvedValue([{ name: "remote", driver: "ceph", status: "Created", description: "" }]);
    const imp = vi.spyOn(api, "importVolumeBackup").mockResolvedValue(ok);
    open("/storage/volumes");
    fireEvent.click(await screen.findByRole("button", { name: "Import backup" }));
    await waitFor(() => expect(dialog().getByLabelText("Pool")).toHaveValue("remote"));
    expect(dialog().queryByLabelText("Member")).toBeNull();
    fireEvent.change(dialog().getByLabelText("Backup file"), { target: { files: [new File(["t"], "v.tar")] } });
    fireEvent.click(dialog().getByRole("button", { name: "Import" }));
    expect(imp).toHaveBeenCalledWith(expect.any(File), { project: "default", pool: "remote", name: "", target: "" }, expect.any(Function));
    fireEvent.click(screen.getByRole("button", { name: "Import backup" }));
    fireEvent.click(dialog().getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("needs a pool", async () => {
    vi.spyOn(api, "fetchStoragePools").mockResolvedValue([]);
    open("/storage/volumes");
    fireEvent.click(await screen.findByRole("button", { name: "Import backup" }));
    fireEvent.change(dialog().getByLabelText("Backup file"), { target: { files: [new File(["t"], "v.tar")] } });
    fireEvent.click(dialog().getByRole("button", { name: "Import" }));
    expect(dialog().getByRole("alert")).toHaveTextContent("Choose a pool.");
  });
});
