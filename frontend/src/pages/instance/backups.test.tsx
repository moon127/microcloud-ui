import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { importRefused, restrictionBlocks } from "../../access";
import * as api from "../../api";
import { renderAt, session } from "../../test/render";
import InstancesPage from "../InstancesPage";
import InstancePage from "./InstancePage";

const ok: api.Started = { operation: null, metadata: null };
const dialog = () => within(screen.getByRole("dialog"));
const inst: api.InstanceFull = {
  project: "web",
  name: "c1",
  type: "container",
  status: "Running",
  location: "m1",
  description: "",
  profiles: [],
  config: {},
  devices: {},
};
const project = (config: Record<string, string> = {}): api.Project => ({
  name: "web",
  description: "",
  config,
  used_by: [],
});
const BACKUPS: api.Backup[] = [
  { name: "b2", created_at: "2026-11-13T10:00:00Z", expires_at: "2026-12-01T00:00:00Z", instance_only: true, optimized_storage: true },
  { name: "b1", created_at: "2026-11-12T10:00:00Z", expires_at: "0001-01-01T00:00:00Z" },
];

function open(s = session()) {
  vi.spyOn(api, "fetchInstance").mockResolvedValue(inst);
  return renderAt(
    <Routes>
      <Route path="/instances/:project/:name" element={<InstancePage />} />
    </Routes>,
    { url: "/instances/web/c1?tab=backups", s },
  );
}

beforeEach(() => {
  vi.spyOn(api, "queryMetric").mockReturnValue(new Promise(() => {}));
  vi.spyOn(api, "fetchMembers").mockResolvedValue([]);
  vi.spyOn(api, "fetchProject").mockResolvedValue(project());
});
afterEach(() => vi.restoreAllMocks());

describe("restrictionBlocks", () => {
  it("follows LXD's default: blocked in a restricted project until allowed", () => {
    expect(restrictionBlocks(undefined, "backups")).toBe(false);
    expect(restrictionBlocks({}, "backups")).toBe(false);
    expect(restrictionBlocks({ restricted: "true" }, "backups")).toBe(true);
    expect(restrictionBlocks({ restricted: "true", "restricted.backups": "allow" }, "backups")).toBe(false);
    expect(restrictionBlocks({ restricted: "true", "restricted.backups": "allow" }, "snapshots")).toBe(true);
  });
});

describe("importRefused", () => {
  it("expects LXD 6.10's refusal in a project with limits or restrictions", () => {
    expect(importRefused()).toBe(false);
    expect(importRefused({ "features.images": "true" })).toBe(false);
    expect(importRefused({ "limits.cpu": "2" })).toBe(true);
    expect(importRefused({ restricted: "true" })).toBe(true);
    expect(importRefused({ restricted: "true", "restricted.containers.lowlevel": "allow" })).toBe(false);
  });
});

describe("BackupsTab", () => {
  it("lists, downloads, renames and deletes backups", async () => {
    vi.spyOn(api, "fetchBackups").mockResolvedValue(BACKUPS);
    const ren = vi.spyOn(api, "renameBackup").mockResolvedValue(ok);
    const del = vi.spyOn(api, "deleteBackup").mockResolvedValue(ok);
    open();
    const region = await screen.findByRole("region", { name: "Backups" });
    const rows = await within(region).findAllByRole("row");
    const b2 = within(rows[1]);
    expect(rows[1]).toHaveTextContent("left out");
    expect(rows[2]).toHaveTextContent("never");
    expect(rows[2]).toHaveTextContent("included");
    expect(b2.getByRole("link", { name: "Download" })).toHaveAttribute("href", "/api/instances/web/c1/backups/b2/export");
    fireEvent.click(b2.getByRole("button", { name: "Rename" }));
    fireEvent.change(dialog().getByLabelText("New name"), { target: { value: "keep" } });
    fireEvent.click(dialog().getByRole("button", { name: "OK" }));
    expect(ren).toHaveBeenCalledWith("web", "c1", "b2", "keep");
    fireEvent.click(b2.getByRole("button", { name: "Delete" }));
    fireEvent.click(dialog().getByRole("button", { name: "Delete" }));
    expect(del).toHaveBeenCalledWith("web", "c1", "b2");
    for (const name of ["Rename", "Delete"]) {
      fireEvent.click(b2.getByRole("button", { name }));
      fireEvent.click(dialog().getByRole("button", { name: "Cancel" }));
    }
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("makes a backup with its options", async () => {
    vi.spyOn(api, "fetchBackups").mockResolvedValue([]);
    const make = vi.spyOn(api, "createBackup").mockResolvedValue(ok);
    open();
    expect(await screen.findByText("No backups.")).toBeInTheDocument();
    const name = screen.getByLabelText("Name") as HTMLInputElement;
    expect(name.value).toMatch(/^backup-\d{8}-\d{4}$/);
    fireEvent.click(screen.getByRole("button", { name: "Make backup" }));
    expect(make).toHaveBeenLastCalledWith("web", "c1", { name: name.value, instance_only: false, optimized_storage: false });
    fireEvent.change(name, { target: { value: "full" } });
    fireEvent.change(screen.getByLabelText("Expires (optional)"), { target: { value: "2026-12-01T10:00" } });
    fireEvent.change(screen.getByLabelText("Compression"), { target: { value: "zstd" } });
    fireEvent.click(screen.getByLabelText(/Leave out snapshots/));
    fireEvent.click(screen.getByLabelText(/Optimized storage/));
    fireEvent.click(screen.getByRole("button", { name: "Make backup" }));
    expect(make).toHaveBeenLastCalledWith("web", "c1", {
      name: "full",
      instance_only: true,
      optimized_storage: true,
      expires_at: new Date("2026-12-01T10:00").toISOString(),
      compression_algorithm: "zstd",
    });
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: " " } });
    expect(screen.getByRole("button", { name: "Make backup" })).toBeDisabled();
  });

  it("says when the restricted project refuses backups", async () => {
    vi.spyOn(api, "fetchProject").mockResolvedValue(project({ restricted: "true" }));
    vi.spyOn(api, "fetchBackups").mockResolvedValue([]);
    open();
    expect(await screen.findByText(/doesn't allow backups/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Make backup" })).toBeNull();
  });

  it("a viewer only sees the list", async () => {
    vi.spyOn(api, "fetchBackups").mockResolvedValue(BACKUPS);
    open(session({ user: { username: "v", role: "project", grants: { web: "viewer" } } }));
    await screen.findByText("b2");
    expect(screen.queryByRole("link", { name: "Download" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Make backup" })).toBeNull();
  });

  it("shows an error", async () => {
    vi.spyOn(api, "fetchBackups").mockRejectedValue(new Error("LXD: boom"));
    open();
    expect(await screen.findByRole("alert")).toHaveTextContent("LXD: boom");
  });
});

describe("Import backup", () => {
  beforeEach(() => {
    vi.spyOn(api, "fetchInstances").mockResolvedValue([]);
    vi.spyOn(api, "fetchProjects").mockResolvedValue([project({ restricted: "true" }), { ...project(), name: "lab" }]);
    vi.spyOn(api, "fetchStoragePools").mockResolvedValue([{ name: "remote", driver: "ceph", status: "Created", description: "" }]);
  });

  it("uploads with progress and creates the instance", async () => {
    let report: (f: number) => void = () => {};
    let finish: (st: api.Started) => void = () => {};
    const imp = vi.spyOn(api, "importBackup").mockImplementation((_f, _t, onProgress) => {
      report = onProgress!;
      return new Promise((resolve) => (finish = resolve));
    });
    // a project admin of web, viewer of lab: only web is offered
    renderAt(<InstancesPage />, { s: session({ user: { username: "p", role: "project", grants: { web: "admin", lab: "viewer" } } }) });
    fireEvent.click(await screen.findByRole("button", { name: "Import backup" }));
    await waitFor(() => expect(dialog().getAllByRole("option").map((o) => o.textContent)).toContain("remote"));
    expect(within(dialog().getByLabelText("Project")).getAllByRole("option").map((o) => o.textContent)).toEqual(["web"]);
    expect(dialog().getByRole("note")).toHaveTextContent("LXD will probably refuse the import");
    fireEvent.click(dialog().getByRole("button", { name: "Import" }));
    expect(dialog().getByRole("alert")).toHaveTextContent("Choose a backup file.");
    const file = new File(["tar"], "c1.tar.gz");
    fireEvent.change(dialog().getByLabelText("Backup file"), { target: { files: [file] } });
    fireEvent.change(dialog().getByLabelText(/^Name/), { target: { value: "../x" } });
    fireEvent.click(dialog().getByRole("button", { name: "Import" }));
    expect(dialog().getByRole("alert")).toHaveTextContent(/Names start with/);
    fireEvent.change(dialog().getByLabelText(/^Name/), { target: { value: "restored" } });
    fireEvent.change(dialog().getByLabelText("Storage pool"), { target: { value: "remote" } });
    fireEvent.click(dialog().getByRole("button", { name: "Import" }));
    expect(imp).toHaveBeenCalledWith(file, { project: "web", name: "restored", pool: "remote" }, expect.any(Function));
    const toasts = screen.getByRole("list", { name: "Actions" });
    act(() => report(0.42));
    expect(toasts).toHaveTextContent("Import c1.tar.gz into web: 42% uploaded");
    act(() => report(1));
    expect(toasts).toHaveTextContent("uploaded; LXD is unpacking it");
    await act(async () => finish(ok));
    await waitFor(() => expect(toasts).toHaveTextContent("✓ Import c1.tar.gz into web"));
    expect(toasts).not.toHaveTextContent("LXD is creating");
  });

  it("is offered only to those who can create, and can be cancelled", async () => {
    const { unmount } = renderAt(<InstancesPage />, { s: session({ user: { username: "v", role: "viewer" } }) });
    await screen.findByRole("heading", { name: "Instances" });
    expect(screen.queryByRole("button", { name: "Import backup" })).toBeNull();
    unmount();
    renderAt(<InstancesPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Import backup" }));
    fireEvent.change(dialog().getByLabelText("Project"), { target: { value: "lab" } });
    expect(dialog().queryByRole("note")).toBeNull();
    fireEvent.click(dialog().getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
