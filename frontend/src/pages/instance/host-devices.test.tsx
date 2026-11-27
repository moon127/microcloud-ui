import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { isHostDevice, mayUseHostDevices } from "../../access";
import * as api from "../../api";
import { renderAt, session } from "../../test/render";
import InstancePage from "./InstancePage";

const ok: api.Started = { operation: null, metadata: null };
const dialog = () => within(screen.getByRole("dialog"));
const NFS = { type: "disk", source: "/srv/nfs", path: "/mnt/nfs", readonly: "true" };
const inst = (over: Partial<api.InstanceFull> = {}): api.InstanceFull => ({
  project: "web",
  name: "c1",
  type: "container",
  status: "Running",
  location: "m1",
  description: "",
  profiles: ["default"],
  config: {},
  devices: { nfs: NFS, kvm: { type: "usb", vendorid: "0557", productid: "2419" }, data: { type: "disk", pool: "remote", source: "v", path: "/d" } },
  expanded_devices: {
    nfs: NFS,
    kvm: { type: "usb", vendorid: "0557", productid: "2419" },
    root: { type: "disk", pool: "remote", path: "/" },
    gpu0: { type: "gpu" },
  },
  ...over,
});
// as LXD reports them on the test cloud's members (the BMC's virtual keyboard), plus a stick
const KVM: api.HostUsb = {
  vendor_id: "0557",
  product_id: "2419",
  vendor: "ATEN International Co., Ltd",
  product: "Virtual mouse/keyboard device",
  serial: "",
  busnum: 1,
  devnum: 5,
  storage: false,
  drivers: ["usbhid"],
};
const STICK: api.HostUsb = { ...KVM, vendor_id: "0781", product_id: "5581", vendor: "SanDisk", product: "Ultra", serial: "4C53", busnum: 2, devnum: 3, storage: true, drivers: [] };
const INV: api.HostInventory = {
  member: "m1",
  usb: [KVM, STICK],
  disks: [
    { id: "sdc", model: "Ultra", size: 2 ** 34, source: "/dev/disk/by-id/usb-SanDisk", partitions: [{ id: "sdc1", size: 2 ** 34, source: "/dev/disk/by-id/usb-SanDisk-part1" }] },
    { id: "sdd", model: "", size: 2 ** 30, source: "/dev/sdd", partitions: [] },
  ],
};
const project = (config: Record<string, string> = {}): api.Project => ({ name: "web", description: "", config, used_by: [] });

function open(i = inst(), s = session()) {
  vi.spyOn(api, "fetchInstance").mockResolvedValue(i);
  return renderAt(
    <Routes>
      <Route path="/instances/:project/:name" element={<InstancePage />} />
    </Routes>,
    { url: "/instances/web/c1?tab=host", s },
  );
}
const pat = (host: string[] = []) => session({ user: { username: "p", role: "project", grants: { web: "admin" }, host_devices: host } });

beforeEach(() => {
  vi.spyOn(api, "queryMetric").mockReturnValue(new Promise(() => {}));
  vi.spyOn(api, "fetchMembers").mockResolvedValue([]);
  vi.spyOn(api, "fetchProject").mockResolvedValue(project());
  vi.spyOn(api, "fetchHostInventory").mockResolvedValue(INV);
});
afterEach(() => vi.restoreAllMocks());

describe("host device helpers", () => {
  it("knows host devices and who may add them", () => {
    expect(isHostDevice(NFS) && isHostDevice({ type: "usb" }) && isHostDevice({ type: "unix-char" })).toBe(true);
    expect(isHostDevice({ type: "disk", pool: "p", source: "v" }) || isHostDevice({ type: "disk", source: "cloud-init:config" })).toBe(false);
    expect(isHostDevice({ type: "disk", path: "/" }) || isHostDevice({ type: "nic" })).toBe(false);
    expect(isHostDevice({ type: "nic", network: "br", parent: "x" }) || isHostDevice({ type: "disk" })).toBe(false);
    // widened in 0.3.4 (D88), as the backend
    const widened: Record<string, string>[] = [
      { type: "disk", source: "ceph:remote/x" },
      { type: "disk", source: "cephfs:fs/p" },
      { type: "proxy" },
      { type: "nic", nictype: "physical", parent: "eth0" },
      { type: "nic", parent: "br0" },
    ];
    for (const d of widened) expect(isHostDevice(d), JSON.stringify(d)).toBe(true);
    expect(mayUseHostDevices({ username: "a", role: "admin" }, "x")).toBe(true);
    expect(mayUseHostDevices({ username: "v", role: "viewer" }, "x")).toBe(false);
    const p = { username: "p", role: "project" as const, grants: { web: "admin" as const, lab: "operator" as const }, host_devices: ["web", "lab"] };
    expect(mayUseHostDevices(p, "web")).toBe(true);
    expect(mayUseHostDevices(p, "lab")).toBe(false);
    expect(mayUseHostDevices({ ...p, host_devices: undefined }, "web")).toBe(false);
  });
});

describe("Host devices tab", () => {
  it("lists the instance's and its profiles' host devices, and sets it to move on evacuation", async () => {
    const patch = vi.spyOn(api, "patchInstanceConfig").mockResolvedValue(ok);
    open();
    const row = (await screen.findByText("nfs")).closest("tr")!;
    expect(row).toHaveTextContent("Host path/srv/nfs → /mnt/nfs, read-only");
    expect(screen.getByText("kvm").closest("tr")).toHaveTextContent("USBUSB 0557:2419");
    expect(screen.getByText("gpu0").closest("tr")).toHaveTextContent("gpugpufrom a profile");
    expect(screen.queryByText("data")).toBeNull();
    expect(screen.queryByText("root")).toBeNull();
    const note = screen.getByRole("note");
    expect(note).toHaveTextContent("On evacuation of m1: LXD stops it (cluster.evacuate is unset, i.e. auto)");
    fireEvent.click(within(note).getByRole("button", { name: /Move it instead/ }));
    expect(patch).toHaveBeenCalledWith("web", "c1", { "cluster.evacuate": "migrate" });
    expect(api.fetchHostInventory).toHaveBeenCalledWith("web", "c1");
  });

  it("says what evacuation does with each cluster.evacuate value", async () => {
    const a = open(inst({ config: { "cluster.evacuate": "migrate" } }));
    expect(await screen.findByRole("note")).toHaveTextContent("it moves (cluster.evacuate=migrate), so the same paths must exist");
    expect(screen.queryByRole("button", { name: /Move it instead/ })).toBeNull();
    a.unmount();
    const b = open(inst({ config: { "cluster.evacuate": "stop" } }));
    expect(await screen.findByRole("note")).toHaveTextContent("it stops (cluster.evacuate=stop)");
    b.unmount();
    const c = open(inst({ config: { "cluster.evacuate": "auto" } }));
    expect(await screen.findByRole("note")).toHaveTextContent("LXD stops it (cluster.evacuate is auto)");
    c.unmount();
    open(inst({ devices: {}, expanded_devices: {}, location: "none", type: "virtual-machine" }));
    expect(await screen.findByText("None.")).toBeInTheDocument();
    expect(screen.getByText(/of the host itself/)).toHaveTextContent("A VM with a host path can't live-migrate.");
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("adds a host path to a container, with checks, a USB disk and 'every member'", async () => {
    const add = vi.spyOn(api, "addHostDevice").mockResolvedValue(ok);
    open();
    fireEvent.click(await screen.findByRole("button", { name: "Add host path" }));
    expect(dialog().getByText(/Evacuating m1 will stop it/)).toBeInTheDocument();
    fireEvent.click(dialog().getByRole("button", { name: "Add" }));
    expect(dialog().getByRole("alert")).toHaveTextContent("absolute, without ..");
    fireEvent.change(dialog().getByLabelText(/^Path on m1/), { target: { value: "/srv/../etc" } });
    fireEvent.click(dialog().getByRole("button", { name: "Add" }));
    expect(dialog().getByRole("alert")).toHaveTextContent("absolute, without ..");
    fireEvent.change(dialog().getByLabelText(/^Path on m1/), { target: { value: "/srv/nfs/Media Files/" } });
    fireEvent.click(dialog().getByRole("button", { name: "Add" }));
    expect(dialog().getByRole("alert")).toHaveTextContent("Choose where to mount it");
    fireEvent.change(dialog().getByLabelText(/^Path inside/), { target: { value: "mnt" } });
    fireEvent.click(dialog().getByRole("button", { name: "Add" }));
    expect(dialog().getByRole("alert")).toHaveTextContent("The path inside is absolute.");
    fireEvent.change(dialog().getByLabelText(/^Path inside/), { target: { value: "/mnt/media" } });
    fireEvent.change(dialog().getByLabelText(/^Device name/), { target: { value: "-x" } });
    fireEvent.click(dialog().getByRole("button", { name: "Add" }));
    expect(dialog().getByRole("alert")).toHaveTextContent("Names start");
    fireEvent.change(dialog().getByLabelText(/^Device name/), { target: { value: "" } });
    fireEvent.click(dialog().getByLabelText(/Shift file ownership/));
    expect(dialog().queryByText(/writes there as the host's root/)).toBeNull(); // read-only
    fireEvent.click(dialog().getByLabelText("Read-only"));
    expect(dialog().getByText(/writes there as the host's root/)).toBeInTheDocument();
    fireEvent.click(dialog().getByLabelText(/Every member has this path/));
    expect(dialog().getByText(/where the path must exist too/)).toBeInTheDocument();
    fireEvent.click(dialog().getByRole("button", { name: "Add" }));
    expect(add).toHaveBeenCalledWith("web", "c1", {
      kind: "path",
      name: "media-files",
      source: "/srv/nfs/Media Files/",
      path: "/mnt/media",
      readonly: false,
      shift: true,
      everywhere: true,
    });
    // a USB disk on the member: its partition, or the whole disk
    fireEvent.click(screen.getByRole("button", { name: "Add host path" }));
    const disks = dialog().getByLabelText("Or a USB disk on m1");
    expect(within(disks).getAllByRole("option").map((o) => o.textContent)).toEqual(["Choose…", "Ultra sdc1 (16.0 GiB)", "sdd (1.0 GiB)"]);
    fireEvent.change(disks, { target: { value: "/dev/disk/by-id/usb-SanDisk-part1" } });
    expect(dialog().getByLabelText(/^Path on m1/)).toHaveValue("/dev/disk/by-id/usb-SanDisk-part1");
    fireEvent.change(dialog().getByLabelText(/^Path inside/), { target: { value: "/mnt/usb" } });
    fireEvent.change(dialog().getByLabelText(/^Device name/), { target: { value: "stick" } });
    fireEvent.click(dialog().getByRole("button", { name: "Add" }));
    expect(add).toHaveBeenLastCalledWith("web", "c1", {
      kind: "path",
      name: "stick",
      source: "/dev/disk/by-id/usb-SanDisk-part1",
      path: "/mnt/usb",
      readonly: true,
    });
    fireEvent.click(screen.getByRole("button", { name: "Add host path" }));
    fireEvent.click(dialog().getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("adds a VM's block device without a path, and falls back to a device name", async () => {
    const add = vi.spyOn(api, "addHostDevice").mockResolvedValue(ok);
    vi.spyOn(api, "fetchHostInventory").mockResolvedValue({ member: "", usb: [], disks: [] });
    open(inst({ type: "virtual-machine", devices: {}, expanded_devices: {} }));
    fireEvent.click(await screen.findByRole("button", { name: "Add host path" }));
    expect(dialog().queryByLabelText(/Shift/)).toBeNull();
    expect(dialog().queryByLabelText(/Or a USB disk/)).toBeNull();
    expect(dialog().getByText(/Evacuating the host will stop it/)).toHaveTextContent("A VM with a host path can't live-migrate.");
    fireEvent.change(dialog().getByLabelText(/^Path on the host/), { target: { value: "/" } });
    fireEvent.click(dialog().getByRole("button", { name: "Add" }));
    expect(add).toHaveBeenCalledWith("web", "c1", { kind: "path", name: "hostpath", source: "/", readonly: true });
  });

  it("adds a USB device: twins by port, serials, storage hints, VMs", async () => {
    const add = vi.spyOn(api, "addHostDevice").mockResolvedValue(ok);
    const twin = { ...KVM, busnum: 2, devnum: 7 };
    vi.spyOn(api, "fetchHostInventory").mockResolvedValue({ ...INV, usb: [KVM, twin, STICK] });
    const a = open(inst({ expanded_devices: undefined, devices: { kvm: { type: "usb", vendorid: "0557", productid: "2419" }, s: { type: "usb", vendorid: "0781", productid: "5581", serial: "other" } } }));
    fireEvent.click(await screen.findByRole("button", { name: "Add USB device" }));
    expect(await dialog().findByText(/USB devices on m1/)).toBeInTheDocument();
    fireEvent.click(dialog().getByRole("button", { name: "Add" }));
    expect(dialog().getByRole("alert")).toHaveTextContent("Choose a device.");
    const radios = dialog().getAllByRole("radio");
    // the instance's kvm device (0557:2419, no serial) matches both twins
    expect(radios[0].closest("label")).toHaveTextContent("(0557:2419, bus 1 device 5, usbhid) (attached to this instance)");
    expect(radios[0]).toBeDisabled();
    expect(radios[2]).toBeEnabled();
    a.unmount();
    open(inst({ devices: {}, expanded_devices: {} }));
    fireEvent.click(await screen.findByRole("button", { name: "Add USB device" }));
    await dialog().findByText(/USB devices on m1/);
    fireEvent.click(dialog().getAllByRole("radio")[1]);
    expect(dialog().getByText(/matched by where it's plugged in/)).toBeInTheDocument();
    expect(dialog().getByText(/only its/)).not.toHaveTextContent("add it as a host path");
    fireEvent.click(dialog().getByRole("button", { name: "Add" }));
    expect(add).toHaveBeenCalledWith("web", "c1", { kind: "usb", name: "virtual-mouse-keyboard-device", vendorid: "0557", productid: "2419", busnum: 2, devnum: 7 });
    fireEvent.click(screen.getByRole("button", { name: "Add USB device" }));
    fireEvent.click(await dialog().findByLabelText(/SanDisk Ultra/));
    expect(dialog().getByText(/add it as a host path instead/)).toBeInTheDocument();
    fireEvent.change(dialog().getByLabelText(/^Device name/), { target: { value: "-x" } });
    fireEvent.click(dialog().getByRole("button", { name: "Add" }));
    expect(dialog().getByRole("alert")).toHaveTextContent("Names start");
    fireEvent.change(dialog().getByLabelText(/^Device name/), { target: { value: "" } });
    fireEvent.click(dialog().getByRole("button", { name: "Add" }));
    expect(add).toHaveBeenLastCalledWith("web", "c1", { kind: "usb", name: "ultra", vendorid: "0781", productid: "5581", serial: "4C53" });
  });

  it("USB: a VM takes the device; no devices; still loading", async () => {
    let resolve: (v: api.HostInventory) => void = () => {};
    vi.spyOn(api, "fetchHostInventory").mockReturnValue(new Promise((r) => (resolve = r)));
    open(inst({ type: "virtual-machine" }));
    fireEvent.click(await screen.findByRole("button", { name: "Add USB device" }));
    expect(dialog().getByText("Reading the host's devices…")).toBeInTheDocument();
    expect(dialog().getByText(/The VM takes the whole device/)).toBeInTheDocument();
    resolve({ member: "m1", usb: [], disks: [] });
    expect(await dialog().findByText("No USB devices on m1.")).toBeInTheDocument();
  });

  it("removes one", async () => {
    const rm = vi.spyOn(api, "removeHostDevice").mockResolvedValue(ok);
    open();
    const row = (await screen.findByText("nfs")).closest("tr")!;
    fireEvent.click(within(row).getByRole("button", { name: "Remove" }));
    expect(dialog().getByText(/even while c1 runs/)).toHaveTextContent("/srv/nfs → /mnt/nfs, read-only");
    fireEvent.click(dialog().getByRole("button", { name: "Cancel" }));
    fireEvent.click(within(row).getByRole("button", { name: "Remove" }));
    fireEvent.click(dialog().getByRole("button", { name: "Remove" }));
    expect(rm).toHaveBeenCalledWith("web", "c1", "nfs");
  });

  it("describes other host devices plainly", async () => {
    open(inst({ devices: { tty: { type: "unix-char", source: "/dev/ttyS0" }, any: { type: "usb" }, b: { type: "usb", vendorid: "1", busnum: "2", devnum: "3" }, sh: { type: "disk", source: "/dev/sdb", shift: "true" } }, expanded_devices: {} }));
    expect((await screen.findByText("tty")).closest("tr")).toHaveTextContent("unix-charunix-char /dev/ttyS0");
    expect(screen.getByText("any").closest("tr")).toHaveTextContent("USB any");
    expect(screen.getByText("b").closest("tr")).toHaveTextContent("USB 1, bus 2 device 3");
    expect(screen.getByText("sh").closest("tr")).toHaveTextContent("/dev/sdb (block device), shifted");
  });

  it("a project admin without the right sees them but can't add or remove, nor read the member's devices", async () => {
    open(inst(), pat());
    expect(await screen.findByText("nfs")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add host path" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Remove" })).toBeNull();
    expect(screen.getByText(/needs the host-devices right in web/)).toBeInTheDocument();
    expect(api.fetchHostInventory).not.toHaveBeenCalled();
  });

  it("with the right, in a restricted project: says what LXD allows there", async () => {
    vi.spyOn(api, "fetchProject").mockResolvedValue(project({ restricted: "true" }));
    const a = open(inst(), pat(["web"]));
    expect(await screen.findByText(/web is restricted/)).toHaveTextContent(
      "LXD refuses host paths until Disk devices are allowed under its Restrictions; LXD refuses USB devices until they're allowed there.",
    );
    expect(screen.getByRole("button", { name: "Add host path" })).toBeInTheDocument();
    a.unmount();
    vi.spyOn(api, "fetchProject").mockResolvedValue(
      project({ restricted: "true", "restricted.devices.disk": "allow", "restricted.devices.disk.paths": "/srv", "restricted.devices.usb": "allow" }),
    );
    const b = open(inst(), pat(["web"]));
    expect(await screen.findByText(/web is restricted/)).toHaveTextContent("host paths under /srv are allowed; USB devices are allowed.");
    b.unmount();
    vi.spyOn(api, "fetchProject").mockResolvedValue(project({ restricted: "true", "restricted.devices.disk": "allow" }));
    open(inst(), pat(["web"]));
    expect(await screen.findByText(/web is restricted/)).toHaveTextContent("host paths anywhere are allowed");
  });

  it("the member's devices unavailable", async () => {
    vi.spyOn(api, "fetchHostInventory").mockRejectedValue(new Error("boom"));
    open();
    await waitFor(() => expect(screen.getByText("m1's devices unavailable: boom")).toBeInTheDocument());
    open(inst({ location: "" }));
    expect(await screen.findByText("The host's devices unavailable: boom")).toBeInTheDocument();
  });
});

describe("finding a host path (D87)", () => {
  const folder = (path: string, entries: api.HostEntry[]): api.HostFolder => ({ member: "m1", path, entries });

  it("offers the member's mounted filesystems", async () => {
    vi.spyOn(api, "fetchHostInventory").mockResolvedValue({ ...INV, mounts: [{ path: "/srv/nfs", fstype: "nfs4", size: 4 * 2 ** 40 }] });
    open();
    fireEvent.click(await screen.findByRole("button", { name: "Add host path" }));
    const sel = await dialog().findByLabelText("Or a filesystem mounted on m1");
    expect(within(sel).getByRole("option", { name: "/srv/nfs (nfs4, 4.0 TiB)" })).toBeInTheDocument();
    fireEvent.change(sel, { target: { value: "/srv/nfs" } });
    expect(dialog().getByLabelText(/^Path on m1/)).toHaveValue("/srv/nfs");
    expect(dialog().queryByRole("button", { name: /Browse/ })).toBeNull(); // the browser is off
  });

  it("browses folders, follows the breadcrumb and picks a folder or a file", async () => {
    vi.spyOn(api, "fetchHostInventory").mockResolvedValue({ ...INV, browse: true });
    const list = vi.spyOn(api, "fetchHostFolder").mockImplementation((_p, _n, path) =>
      Promise.resolve(
        path === "/"
          ? folder("/", [
              { name: "srv", type: "dir", size: 4096, mtime: 1 },
              { name: "lib", type: "link", size: 7, mtime: 1 },
              { name: "null", type: "other", size: 0, mtime: 1 },
            ])
          : path === "/srv"
            ? folder("/srv", [
                { name: "nfs", type: "dir", size: 4096, mtime: 1, mount: "nfs4" },
                { name: "notes.txt", type: "file", size: 2048, mtime: 1 },
              ])
            : folder(path, []),
      ),
    );
    open();
    fireEvent.click(await screen.findByRole("button", { name: "Add host path" }));
    fireEvent.click(await dialog().findByRole("button", { name: "Browse m1…" }));
    const browser = () => within(dialog().getByRole("group", { name: "Folders on the member" }));
    expect(await browser().findByText("lib (link)")).toBeInTheDocument();
    expect(browser().getByText("null (special file)")).toBeInTheDocument();
    fireEvent.click(browser().getByRole("button", { name: "📁 srv/" }));
    expect(await browser().findByText("nfs4 mount")).toBeInTheDocument();
    expect(browser().getByText("2.0 KiB")).toBeInTheDocument();
    fireEvent.click(browser().getByRole("button", { name: "📁 nfs/" }));
    expect(await browser().findByText(/Empty, or nothing readable/)).toBeInTheDocument();
    expect(list).toHaveBeenLastCalledWith("web", "c1", "/srv/nfs");
    const crumbs = within(browser().getByRole("navigation", { name: "Folder path" }));
    expect(crumbs.getByRole("button", { name: "nfs" })).toBeDisabled();
    fireEvent.click(crumbs.getByRole("button", { name: "srv" }));
    fireEvent.click(await browser().findByRole("button", { name: "notes.txt" }));
    expect(dialog().getByLabelText(/^Path on m1/)).toHaveValue("/srv/notes.txt");
    expect(dialog().queryByRole("group", { name: "Folders on the member" })).toBeNull();
    // opens where the typed path is; "Use" takes the folder shown
    fireEvent.click(dialog().getByRole("button", { name: "Browse m1…" }));
    await waitFor(() => expect(list).toHaveBeenLastCalledWith("web", "c1", "/srv/notes.txt"));
    fireEvent.click(within(dialog().getByRole("navigation", { name: "Folder path" })).getByRole("button", { name: "/" }));
    fireEvent.click(await browser().findByRole("button", { name: "Use /" }));
    expect(dialog().getByLabelText(/^Path on m1/)).toHaveValue("/");
  });

  it("a project admin starts in the project's allowed folder and is told what's hidden", async () => {
    vi.spyOn(api, "fetchHostInventory").mockResolvedValue({ ...INV, browse: true, allowed: ["/srv/nfs"], hidden: ["/home", "/etc"] });
    vi.spyOn(api, "fetchHostFolder").mockRejectedValue(new Error("/srv/nfs is hidden from project admins"));
    open(inst(), pat(["web"]));
    fireEvent.click(await screen.findByRole("button", { name: "Add host path" }));
    expect(await dialog().findByText("web allows host paths only under /srv/nfs.")).toBeInTheDocument();
    expect(dialog().getByText("Hidden from project admins: /home, /etc.")).toBeInTheDocument();
    fireEvent.click(dialog().getByRole("button", { name: "Browse m1…" }));
    expect(await dialog().findByRole("alert")).toHaveTextContent("hidden from project admins");
    expect(api.fetchHostFolder).toHaveBeenCalledWith("web", "c1", "/srv/nfs");
  });

  it("says the helper may be starting", async () => {
    vi.spyOn(api, "fetchHostInventory").mockResolvedValue({ ...INV, browse: true });
    vi.spyOn(api, "fetchHostFolder").mockReturnValue(new Promise(() => {}));
    open();
    fireEvent.click(await screen.findByRole("button", { name: "Add host path" }));
    fireEvent.click(await dialog().findByRole("button", { name: "Browse m1…" }));
    expect(await dialog().findByRole("status")).toHaveTextContent("Listing /… (the first time starts the helper)");
  });
});
