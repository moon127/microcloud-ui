import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { renderAt, session } from "../../test/render";
import CreateInstancePage from "./CreateInstancePage";
import { buildRequest, emptyForm, problems } from "./request";

afterEach(() => vi.restoreAllMocks());

describe("request builder", () => {
  it("maps the form to the API body", () => {
    const f = {
      ...emptyForm("lab"),
      name: "web1",
      target: "m2",
      image: { remote: "images" as const, alias: "alpine/edge" },
      description: "d",
      profiles: ["default", "big"],
      cpu: " 2 ",
      memory: "1GiB",
      rootPool: "remote",
      rootSize: "10GiB",
      nics: [{ name: "eth0", network: "ovn1", ipv4: " 10.0.0.5 " }, { name: "eth1", network: "br0", ipv4: "" }],
      disks: [{ name: "data", pool: "remote", volume: "vol1", path: "/data" }],
      userData: "#cloud-config\n",
      vendorData: " ",
      nesting: true,
      autostart: true,
      start: false,
    };
    expect(problems(f)).toEqual([]);
    expect(buildRequest(f)).toEqual({
      name: "web1",
      type: "container",
      project: "lab",
      target: "m2",
      image: { remote: "images", alias: "alpine/edge" },
      description: "d",
      profiles: ["default", "big"],
      config: {
        "limits.cpu": "2",
        "limits.memory": "1GiB",
        "cloud-init.user-data": "#cloud-config",
        "boot.autostart": "true",
        "security.nesting": "true",
      },
      devices: {
        root: { type: "disk", path: "/", pool: "remote", size: "10GiB" },
        eth0: { type: "nic", network: "ovn1", "ipv4.address": "10.0.0.5" },
        eth1: { type: "nic", network: "br0" },
        data: { type: "disk", pool: "remote", source: "vol1", path: "/data" },
      },
      start: false,
    });
  });

  it("VM options and defaults", () => {
    const vm = { ...emptyForm("default"), name: "v", type: "virtual-machine" as const, secureboot: false, nesting: true, rootSize: "20GiB" };
    const r = buildRequest(vm);
    expect(r.config).toEqual({ "security.secureboot": "false" }); // nesting ignored for VMs
    expect(r.devices).toEqual({ root: { type: "disk", path: "/", size: "20GiB" } });
    expect(r.image).toEqual({ remote: "local" });
    expect(r).not.toHaveProperty("target");
    expect(buildRequest({ ...vm, secureboot: true, rootSize: "", rootPool: "p" }).config).toEqual({});
    expect(buildRequest({ ...vm, rootSize: "", rootPool: "p" }).devices.root).toEqual({ type: "disk", path: "/", pool: "p" });
  });

  it("explains what's missing", () => {
    expect(problems(emptyForm("x"))).toEqual(["Name: letters, digits and dashes, starting with a letter", "Choose an image"]);
    const f = {
      ...emptyForm("x"),
      name: "ok",
      image: { remote: "local" as const, alias: "a" },
      nics: [{ name: "root", network: "", ipv4: "" }, { name: "", network: "n", ipv4: "" }],
      disks: [{ name: "root", pool: "", volume: "v", path: "/x" }],
    };
    expect(problems(f)).toEqual([
      "Every NIC and disk needs a device name",
      "Device names must be unique (and not 'root')",
      "Choose a network for every NIC",
      "Every extra disk needs a pool, volume and path",
    ]);
    expect(problems({ ...f, name: "9bad" })[0]).toMatch(/^Name/);
  });
});

const remote: api.RemoteImage[] = [
  { alias: "alpine/edge", aliases: [], os: "Alpine", release: "edge", release_title: "edge", variant: "default", arch: "amd64", container: true, vm: false, version: "1" },
  { alias: "debian/12", aliases: [], os: "Debian", release: "12", release_title: "12", variant: "default", arch: "amd64", container: true, vm: true, version: "1" },
];
const local: api.LocalImage[] = [
  {
    fingerprint: "abcdef0123456789",
    aliases: ["golden"],
    description: "Golden",
    type: "container",
    architecture: "x86_64",
    size: 1024,
    created_at: "",
    uploaded_at: "",
    last_used_at: "",
    cached: false,
    public: false,
    auto_update: false,
    properties: {},
    project: "default",
  },
  { ...({} as api.LocalImage), fingerprint: "ffff00001111", aliases: [], description: "", type: "virtual-machine", architecture: "x86_64", size: 1, properties: { os: "Ubuntu" } },
];

beforeEach(() => {
  vi.spyOn(api, "fetchProjects").mockResolvedValue([
    { name: "default", description: "", config: {}, used_by: [] },
    { name: "mcui-test", description: "", config: {}, used_by: [] },
  ]);
  vi.spyOn(api, "fetchMembers").mockResolvedValue([
    { name: "m1", url: "", status: "Online", roles: [], failure_domain: "", message: "", architecture: "" },
  ]);
  vi.spyOn(api, "fetchStoragePools").mockResolvedValue([{ name: "remote", driver: "ceph", status: "", description: "" }]);
  vi.spyOn(api, "fetchProfiles").mockResolvedValue([
    { name: "default", description: "", config: {}, devices: {}, used_by: [], project: "default" },
    { name: "big", description: "4 CPUs", config: {}, devices: {}, used_by: [], project: "default" },
  ]);
  vi.spyOn(api, "fetchNetworks").mockResolvedValue([
    { name: "ovn1", type: "ovn", managed: true, description: "", status: "", config: {} },
    { name: "eno1", type: "physical", managed: false, description: "", status: "", config: {} },
  ]);
  vi.spyOn(api, "fetchRemoteImages").mockResolvedValue(remote);
  vi.spyOn(api, "fetchLocalImages").mockResolvedValue(local);
});

function open(s = session()) {
  return renderAt(
    <Routes>
      <Route path="/instances/new" element={<CreateInstancePage />} />
      <Route path="/instances/:project/:name" element={<p>instance page</p>} />
      <Route path="/instances" element={<p>instance list</p>} />
    </Routes>,
    { url: "/instances/new", s },
  );
}

describe("CreateInstancePage", () => {
  it("creates a container from a remote image and opens it", async () => {
    const create = vi.spyOn(api, "createInstance").mockResolvedValue({ operation: null, metadata: null });
    open();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "web1" } });
    await screen.findByRole("option", { name: "mcui-test" });
    fireEvent.change(screen.getByLabelText("Project"), { target: { value: "mcui-test" } });
    fireEvent.click(await screen.findByRole("button", { name: "Use images:alpine/edge" }));
    expect(screen.getByRole("button", { name: "Use images:alpine/edge" })).toHaveTextContent("Selected");
    fireEvent.change(screen.getByLabelText("Search images"), { target: { value: "debian" } });
    expect(screen.queryByText("images:alpine/edge")).toBeNull();
    fireEvent.change(screen.getByLabelText("Member"), { target: { value: "m1" } });
    fireEvent.click(await screen.findByLabelText(/big/));
    fireEvent.click(screen.getByLabelText(/default/));
    fireEvent.change(screen.getByLabelText("Memory limit"), { target: { value: "256MiB" } });
    fireEvent.change(screen.getByLabelText("Root disk size"), { target: { value: "2GiB" } });
    fireEvent.click(screen.getByLabelText("Allow nesting"));
    fireEvent.click(screen.getByLabelText("Start after creating"));
    fireEvent.change(screen.getByLabelText("User data"), { target: { value: "#cloud-config" } });
    expect(within(screen.getByLabelText("Request preview")).getByText(/cloud-init.user-data/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(create).toHaveBeenCalledWith({
      name: "web1",
      type: "container",
      project: "mcui-test",
      target: "m1",
      image: { remote: "images", alias: "alpine/edge" },
      description: "",
      profiles: ["big"],
      config: { "limits.memory": "256MiB", "cloud-init.user-data": "#cloud-config", "security.nesting": "true" },
      devices: { root: { type: "disk", path: "/", size: "2GiB" } },
      start: false,
    });
    expect(await screen.findByText("instance page")).toBeInTheDocument();
  });

  it("shows what's missing, then creates a VM from a local image with NICs and disks", async () => {
    const create = vi.spyOn(api, "createInstance").mockResolvedValue({ operation: null, metadata: null });
    open();
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Choose an image");
    expect(create).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "vm1" } });
    fireEvent.click(screen.getByLabelText("VM"));
    expect(screen.queryByLabelText("Allow nesting")).toBeNull();
    fireEvent.click(screen.getByLabelText("Secure boot"));
    fireEvent.click(screen.getByRole("button", { name: "Local images" }));
    expect(await screen.findByText("ffff00001111")).toBeInTheDocument(); // VM image without alias
    expect(screen.queryByText("golden")).toBeNull(); // container image hidden for VMs
    fireEvent.click(screen.getByRole("button", { name: "Use ffff00001111" }));

    fireEvent.click(screen.getByRole("button", { name: "Add NIC" }));
    fireEvent.click(screen.getByRole("button", { name: "Add NIC" }));
    const nicNet = screen.getAllByLabelText("Network");
    expect(within(nicNet[0]).queryByRole("option", { name: /eno1/ })).toBeNull(); // unmanaged hidden
    fireEvent.change(nicNet[0], { target: { value: "ovn1" } });
    fireEvent.change(screen.getAllByLabelText("Static IPv4 (optional)")[0], { target: { value: "10.1.1.9" } });
    fireEvent.change(screen.getAllByLabelText("Device")[1], { target: { value: "eth9" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Remove" })[1]);
    fireEvent.click(screen.getByRole("button", { name: "Add disk" }));
    fireEvent.change(screen.getByLabelText("Pool"), { target: { value: "remote" } });
    fireEvent.change(screen.getByLabelText("Custom volume"), { target: { value: "vol1" } });
    fireEvent.change(screen.getByLabelText("Mount path"), { target: { value: "/data" } });
    fireEvent.change(screen.getByLabelText("Root disk pool"), { target: { value: "remote" } });
    fireEvent.change(screen.getByLabelText("CPU limit"), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText("Or cluster group"), { target: { value: "gpu" } });
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "test vm" } });
    fireEvent.change(screen.getByLabelText("Vendor data"), { target: { value: "v" } });
    fireEvent.change(screen.getByLabelText("Network config"), { target: { value: "n" } });
    fireEvent.click(screen.getByLabelText("Start on host boot"));
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(create).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "vm1",
        type: "virtual-machine",
        target: "@gpu",
        description: "test vm",
        image: { remote: "local", fingerprint: "ffff00001111" },
        config: {
          "limits.cpu": "2",
          "cloud-init.vendor-data": "v",
          "cloud-init.network-config": "n",
          "boot.autostart": "true",
          "security.secureboot": "false",
        },
        devices: {
          root: { type: "disk", path: "/", pool: "remote" },
          eth0: { type: "nic", network: "ovn1", "ipv4.address": "10.1.1.9" },
          disk1: { type: "disk", pool: "remote", source: "vol1", path: "/data" },
        },
      }),
    );
  });

  it("shows progress while LXD creates and starts the instance", async () => {
    const running = (metadata: api.Operation["metadata"]): api.Operation => ({
      id: "op9",
      description: "Creating instance",
      created_at: "",
      status: "Running",
      metadata,
    });
    let finish!: (o: api.Operation) => void;
    vi.spyOn(api, "createInstance").mockResolvedValue({ operation: running(null), metadata: null });
    vi.spyOn(api, "waitOperation")
      .mockResolvedValueOnce(running({ download_progress: "rootfs: 45% (12.3MB/s)" }))
      .mockReturnValueOnce(new Promise((r) => (finish = r)));
    open();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "web2" } });
    fireEvent.click(await screen.findByRole("button", { name: "Use images:alpine/edge" }));
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("Creating web2 and starting it… Sending the request to LXD");
    expect(screen.getByRole("button", { name: "Creating…" })).toBeDisabled();
    expect(await screen.findByText(/rootfs: 45% \(12.3MB\/s\)/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Operations" })).toHaveAttribute("href", "/operations");
    finish({ ...running(null), status: "Success" });
    expect(await screen.findByText("instance page")).toBeInTheDocument();
  });

  it("shows the operation's description when there's no progress note", async () => {
    vi.spyOn(api, "createInstance").mockResolvedValue({
      operation: { id: "o", description: "Creating instance", created_at: "", status: "Running" },
      metadata: null,
    });
    vi.spyOn(api, "waitOperation").mockReturnValue(new Promise(() => {}));
    open();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "web3" } });
    fireEvent.click(await screen.findByRole("button", { name: "Use images:alpine/edge" }));
    fireEvent.click(screen.getByLabelText("Start after creating"));
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Creating web3… Creating instance"));
  });

  it("stays on the page when creation fails; cancel goes back", async () => {
    vi.spyOn(api, "createInstance").mockRejectedValue(new Error("Instance limit reached"));
    open();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "x1" } });
    fireEvent.click(await screen.findByRole("button", { name: "Use images:debian/12" }));
    fireEvent.click(screen.getByLabelText("Privileged"));
    fireEvent.change(screen.getByLabelText("Or cluster group"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByText("✗ Create x1: Instance limit reached")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Creating x1 failed: Instance limit reached");
    expect(screen.getByRole("button", { name: "Create" })).toBeEnabled();
    expect(screen.queryByText("instance page")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByText("instance list")).toBeInTheDocument();
  });

  it("local images with an alias, and picker states", async () => {
    vi.spyOn(api, "fetchRemoteImages").mockRejectedValueOnce(new Error("offline"));
    open();
    expect(await screen.findByRole("alert")).toHaveTextContent("offline");
    fireEvent.click(screen.getByRole("button", { name: "Local images" }));
    expect(await screen.findByText(/Golden · x86_64 · 1.0 KiB/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Use golden" }));
    fireEvent.change(screen.getByLabelText("Search images"), { target: { value: "zzz" } });
    expect(screen.getByText("No container images match.")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("VM"));
    expect(screen.getByText("No VM images match.")).toBeInTheDocument();
    vi.spyOn(api, "fetchRemoteImages").mockReturnValue(new Promise(() => {}));
    fireEvent.click(screen.getByRole("button", { name: "ubuntu: (official)" }));
    expect(screen.getByRole("status")).toHaveTextContent("Loading images");
  });

  it("is admin-only", () => {
    open(session({ user: { username: "v", role: "viewer" } }));
    expect(screen.getByRole("alert")).toHaveTextContent("needs the admin role");
  });

  it("links from the instance list for admins", async () => {
    vi.spyOn(api, "fetchInstances").mockResolvedValue([]);
    const { default: InstancesPage } = await import("../InstancesPage");
    renderAt(<InstancesPage />);
    await waitFor(() => expect(screen.getByRole("link", { name: "Create instance" })).toHaveAttribute("href", "/instances/new"));
  });
});
