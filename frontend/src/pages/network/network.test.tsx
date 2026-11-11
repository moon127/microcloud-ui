import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { renderAt, session } from "../../test/render";
import NetworksPage from "../NetworksPage";
import CreateNetworkPage from "./CreateNetworkPage";
import NetworkPage from "./NetworkPage";
import { buildNetwork, emptyNetwork, networkProblems } from "./request";

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.removeItem("mcui.project");
});

const ok: api.Started = { operation: null, metadata: null };
const viewer = () => session({ user: { username: "v", role: "viewer" } });
const member = (name: string): api.Member => ({ name, url: "", status: "Online", roles: [], failure_domain: "", message: "", architecture: "" });
const net = (over: Partial<api.Network> = {}): api.Network => ({
  name: "n",
  type: "bridge",
  managed: true,
  description: "",
  status: "Created",
  config: {},
  ...over,
});

function routes(url: string, s = session()) {
  return renderAt(
    <Routes>
      <Route path="/networks" element={<NetworksPage />} />
      <Route path="/networks/new" element={<CreateNetworkPage />} />
      <Route path="/networks/:project/:name" element={<NetworkPage />} />
    </Routes>,
    { url, s },
  );
}

describe("network request builder", () => {
  it("keeps only filled-in fields and per-member keys", () => {
    const f = {
      ...emptyNetwork("p"),
      name: " up1 ",
      type: "physical" as const,
      description: " d ",
      fields: { mtu: " 9000 ", vlan: "", "ipv4.address": "ignored for physical" },
      perMember: { m1: " eno1 ", m2: "eno2" },
      extra: { "ipv4.routes.external": "10.0.0.0/8" },
    };
    expect(networkProblems(f, ["m1", "m2"])).toEqual([]);
    expect(buildNetwork(f, ["m1", "m2"])).toEqual({
      name: "up1",
      type: "physical",
      project: "p",
      description: "d",
      config: { "ipv4.routes.external": "10.0.0.0/8", mtu: "9000" },
      members: { m1: { parent: "eno1" }, m2: { parent: "eno2" } },
    });
  });

  it("OVN takes its uplink; bridges' external interfaces are optional", () => {
    const ovn = { ...emptyNetwork("default"), name: "o1", type: "ovn" as const, fields: { network: "UPLINK", "ipv4.nat": "true" } };
    expect(buildNetwork(ovn, ["m1"]).config).toEqual({ network: "UPLINK", "ipv4.nat": "true" });
    expect(buildNetwork(ovn, ["m1"]).members).toEqual({});
    const br = { ...emptyNetwork("default"), name: "b", perMember: { m1: "eth9", m2: " " } };
    expect(buildNetwork(br, ["m1", "m2"]).members).toEqual({ m1: { "bridge.external_interfaces": "eth9" } });
    expect(networkProblems(br, ["m1", "m2"])).toEqual([]);
  });

  it("explains what's missing", () => {
    expect(networkProblems({ ...emptyNetwork("x"), name: "much-too-long-name" }, [])).toEqual([
      "Name: up to 15 letters, digits and dashes, starting with a letter",
    ]);
    expect(networkProblems({ ...emptyNetwork("x"), name: "m", type: "macvlan", perMember: { a: "e" } }, ["a", "b"])).toEqual([
      "Parent interface: needed on every member",
    ]);
    expect(networkProblems({ ...emptyNetwork("x"), name: "o", type: "ovn" }, [])).toEqual(["Choose an uplink network"]);
  });
});

describe("NetworksPage list", () => {
  beforeEach(() => {
    vi.spyOn(api, "fetchNetworkTraffic").mockResolvedValue({ start: 0, end: 1, resolution: 0, rx: [], tx: [] });
    vi.spyOn(api, "queryMetric").mockResolvedValue({ name: "", fn: "value", start: 0, end: 1, resolution: 0, series: [] } as never);
  });

  it("lists managed networks and each member's own interfaces", async () => {
    vi.spyOn(api, "fetchMembers").mockResolvedValue([member("m1"), member("m2")]);
    const fetch = vi.spyOn(api, "fetchNetworks").mockImplementation((_p, target) =>
      Promise.resolve(
        target
          ? [net({ name: `eth-${target}`, type: "physical", managed: false }), net({ name: "br0" })]
          : [
              net({ name: "br0", config: { "ipv4.address": "10.0.0.1/24", parent: "x" }, used_by: 2, description: "lab" }),
              net({ name: "o1", type: "ovn", config: { network: "UPLINK", "ipv4.address": "10.1.0.1/24" } }),
              net({ name: "UPLINK", type: "physical", config: { "ipv4.gateway": "192.0.2.1/24", "ipv4.ovn.ranges": "192.0.2.50-192.0.2.99" } }),
              net({ name: "mv", type: "macvlan" }),
              net({ name: "eno1", managed: false }),
            ],
      ),
    );
    routes("/networks");
    const row = (await screen.findByRole("link", { name: "br0" })).closest("tr")!;
    expect(row).toHaveTextContent("10.0.0.1/24 · x");
    expect(row).toHaveTextContent("2");
    expect(screen.getByRole("link", { name: "o1" }).closest("tr")).toHaveTextContent("uplink UPLINK · 10.1.0.1/24");
    expect(screen.getByRole("link", { name: "UPLINK" }).closest("tr")).toHaveTextContent("gateway 192.0.2.1/24 · OVN 192.0.2.50-192.0.2.99");
    expect(screen.getByRole("link", { name: "mv" }).closest("tr")).toHaveTextContent("–");
    expect(screen.queryByRole("link", { name: "eno1" })).toBeNull();
    expect(await screen.findByRole("link", { name: /eth-m1/ })).toHaveAttribute("href", "/networks/default/eth-m1?member=m1");
    fireEvent.change(screen.getByLabelText("Member"), { target: { value: "m2" } });
    expect(await screen.findByRole("link", { name: /eth-m2/ })).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith("default", "m2");
    expect(screen.getByRole("link", { name: "Create network" })).toHaveAttribute("href", "/networks/new");
  });

  it("viewer, empty and error states", async () => {
    vi.spyOn(api, "fetchMembers").mockResolvedValue([member("m1")]);
    vi.spyOn(api, "fetchNetworks").mockResolvedValue([]);
    const a = routes("/networks", viewer());
    expect(await screen.findByText("No managed networks.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Create network" })).toBeNull();
    a.unmount();
    vi.spyOn(api, "fetchNetworks").mockRejectedValue(new Error("down"));
    vi.spyOn(api, "fetchMembers").mockResolvedValue([]);
    routes("/networks");
    expect((await screen.findAllByRole("alert"))[0]).toHaveTextContent("down");
  });
});

describe("CreateNetworkPage", () => {
  beforeEach(() => {
    vi.spyOn(api, "fetchMembers").mockResolvedValue([member("m1"), member("m2")]);
    vi.spyOn(api, "fetchNetworks").mockImplementation((_p, target) =>
      Promise.resolve(
        target
          ? [net({ name: `eno-${target}`, type: "physical", managed: false })]
          : [net({ name: "UPLINK", type: "physical" }), net({ name: "br0" })],
      ),
    );
    vi.spyOn(api, "fetchNetwork").mockResolvedValue({ ...net(), used_by: [], locations: [] });
    vi.spyOn(api, "fetchNetworkState").mockReturnValue(new Promise(() => {}));
    vi.spyOn(api, "fetchNetworkMembers").mockResolvedValue([]);
    vi.spyOn(api, "fetchNetworkAllocations").mockResolvedValue([]);
    vi.spyOn(api, "fetchNetworkLeases").mockResolvedValue([]);
  });

  it("creates a physical network with a parent on every member", async () => {
    const create = vi.spyOn(api, "createNetwork").mockResolvedValue(ok);
    routes("/networks/new");
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Name: up to 15");
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "up2" } });
    fireEvent.click(screen.getByRole("radio", { name: /^Physical/ }));
    const m1 = await screen.findByLabelText("m1");
    await waitFor(() => expect(document.querySelector("#ifaces-m1 option")).toHaveValue("eno-m1"));
    expect(screen.getByRole("button", { name: "Use m1's value on every member" })).toBeDisabled();
    fireEvent.change(m1, { target: { value: "bond0.100" } });
    fireEvent.click(screen.getByRole("button", { name: "Use m1's value on every member" }));
    expect(screen.getByLabelText("m2")).toHaveValue("bond0.100");
    fireEvent.change(screen.getByLabelText("VLAN id"), { target: { value: "100" } });
    fireEvent.change(screen.getByLabelText(/^Other settings/), { target: { value: "[x" } });
    fireEvent.change(screen.getByLabelText(/^Other settings/), { target: { value: "- a" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(screen.getByRole("alert")).toHaveTextContent("config must be a mapping");
    fireEvent.change(screen.getByLabelText(/^Other settings/), { target: { value: "ipv4.gateway: 192.0.2.1/24" } });
    expect(within(screen.getByLabelText("Request preview")).getByText(/bond0.100/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(create).toHaveBeenCalledWith({
      name: "up2",
      type: "physical",
      project: "default",
      description: "",
      config: { "ipv4.gateway": "192.0.2.1/24", vlan: "100" },
      members: { m1: { parent: "bond0.100" }, m2: { parent: "bond0.100" } },
    });
    expect(await screen.findByRole("heading", { name: /Network up2/ })).toBeInTheDocument();
  });

  it("creates an OVN network on an uplink; shows LXD's refusal", async () => {
    const create = vi.spyOn(api, "createNetwork").mockRejectedValue(new Error("Uplink has no ranges"));
    routes("/networks/new");
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "o2" } });
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "app net" } });
    fireEvent.click(screen.getByRole("radio", { name: /^OVN/ }));
    expect(screen.queryByLabelText("m1")).toBeNull();
    await screen.findByRole("option", { name: "UPLINK" });
    expect(screen.queryByRole("option", { name: "br0" })).toBeNull();
    fireEvent.change(screen.getByLabelText("Uplink network"), { target: { value: "UPLINK" } });
    fireEvent.change(screen.getByLabelText("IPv4 NAT"), { target: { value: "false" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(create).toHaveBeenCalledWith(
      expect.objectContaining({ type: "ovn", description: "app net", config: { network: "UPLINK", "ipv4.nat": "false" }, members: {} }),
    );
    expect(await screen.findByText("Creating the network failed: Uplink has no ranges")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(await screen.findByRole("heading", { name: "Networks" })).toBeInTheDocument();
  });

  it("needs the admin role", () => {
    routes("/networks/new", viewer());
    expect(screen.getByRole("alert")).toHaveTextContent("needs the admin role");
  });
});

const detail = (over: Partial<api.NetworkDetail> = {}): api.NetworkDetail => ({
  name: "br0",
  type: "bridge",
  managed: true,
  description: "lab",
  status: "Created",
  config: { "ipv4.address": "10.0.0.1/24" },
  used_by: ["/1.0/instances/c1", "/1.0/networks/o1", "/1.0/profiles/default?project=lab"],
  locations: ["m1", "m2"],
  ...over,
});

const state = (over: Partial<api.NetworkState> = {}): api.NetworkState => ({
  addresses: [{ address: "10.0.0.1", family: "inet", netmask: "24", scope: "global" }],
  counters: { bytes_received: 2048, bytes_sent: 1024, packets_received: 20, packets_sent: 10 },
  hwaddr: "00:16:3e:00:00:01",
  mtu: 1500,
  state: "up",
  type: "broadcast",
  ...over,
});

describe("NetworkPage", () => {
  it("shows and edits a managed network", async () => {
    vi.spyOn(api, "fetchNetwork").mockResolvedValue(detail());
    const st = vi.spyOn(api, "fetchNetworkState").mockImplementation((_p, _n, target) =>
      Promise.resolve(
        target === "m2"
          ? state({ ovn: { chassis: "m2" }, vlan: { lower_device: "bond0", vid: 7 }, addresses: null, counters: null, hwaddr: "" })
          : state({ bond: { mode: "802.3ad", lower_devices: ["eno1", "eno2"] }, bridge: { upper_devices: ["veth1"] } }),
      ),
    );
    vi.spyOn(api, "fetchNetworkMembers").mockResolvedValue([
      { member: "m1", config: { "bridge.external_interfaces": "eth2" } },
      { member: "m2", config: {}, error: "Member offline" },
    ]);
    vi.spyOn(api, "fetchNetworkAllocations").mockResolvedValue([
      { addresses: "10.0.0.5/32", used_by: "/1.0/instances/c1", type: "instance", nat: false, hwaddr: "00:16:3e:aa", network: "br0" },
      { addresses: "10.0.0.1/24", used_by: "/1.0/networks/br0", type: "network", nat: true, hwaddr: "", network: "br0" },
      { addresses: "10.9.0.1/24", used_by: "/1.0/networks/other", type: "network", nat: true, hwaddr: "", network: "other" },
    ]);
    vi.spyOn(api, "fetchNetworkLeases").mockResolvedValue([
      { hostname: "c1", address: "10.0.0.5", hwaddr: "00:16:3e:aa", type: "dynamic", location: "m1", project: "default" },
      { hostname: "br0.gw", address: "10.0.0.1", hwaddr: "", type: "gateway", location: "", project: "default" },
    ]);
    const put = vi.spyOn(api, "updateNetwork").mockResolvedValue(ok);
    const del = vi.spyOn(api, "deleteNetwork").mockResolvedValue(ok);
    vi.spyOn(api, "fetchNetworks").mockResolvedValue([]);
    vi.spyOn(api, "fetchMembers").mockResolvedValue([]);
    vi.spyOn(api, "fetchNetworkTraffic").mockResolvedValue({ start: 0, end: 1, resolution: 0, rx: [], tx: [] });
    vi.spyOn(api, "queryMetric").mockResolvedValue({ name: "", fn: "value", start: 0, end: 1, resolution: 0, series: [] } as never);
    routes("/networks/default/br0");
    expect(await screen.findByRole("heading", { name: "Network br0 bridge · in default" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "m2" })).toHaveAttribute("href", "/members/m2");
    expect(screen.getByRole("link", { name: "network o1" })).toHaveAttribute("href", "/networks/default/o1");
    expect(screen.getByRole("link", { name: "profile lab/default" })).toHaveAttribute("href", "/profiles/lab/default");

    // state, switching member
    expect(await screen.findByText("802.3ad over eno1, eno2")).toBeInTheDocument();
    expect(screen.getByText("veth1")).toBeInTheDocument();
    expect(screen.getByText("2.0 KiB (20 packets)")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("On member"), { target: { value: "m2" } });
    expect(await screen.findByText("7 on bond0")).toBeInTheDocument();
    expect(st).toHaveBeenLastCalledWith("default", "br0", "m2");

    // addresses and leases
    expect(await screen.findByText("10.0.0.5/32")).toBeInTheDocument();
    expect(screen.queryByText("10.9.0.1/24")).toBeNull();
    expect(screen.getByText("br0.gw")).toBeInTheDocument();

    // per-member settings
    expect(await screen.findByText("m2: Member offline")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("m1"), { target: { value: "- x" } });
    expect(screen.getByRole("button", { name: "Save m1" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("m1"), { target: { value: "bridge.external_interfaces: eth3" } });
    fireEvent.click(screen.getByRole("button", { name: "Save m1" }));
    expect(put).toHaveBeenCalledWith("default", "br0", { config: { "bridge.external_interfaces": "eth3" } }, "m1");

    // cluster-wide config
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "edited" } });
    fireEvent.change(screen.getByLabelText("Config (YAML), cluster-wide"), { target: { value: "- bad" } });
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Config (YAML), cluster-wide"), { target: { value: "ipv4.nat: true" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(put).toHaveBeenLastCalledWith("default", "br0", { description: "edited", config: { "ipv4.nat": "true" } });

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }));
    expect(del).toHaveBeenCalledWith("default", "br0");
    expect(await screen.findByRole("heading", { name: "Networks" })).toBeInTheDocument();
  });

  it("an unmanaged interface on one member, read-only", async () => {
    const get = vi.spyOn(api, "fetchNetwork").mockResolvedValue(detail({ name: "eno1", type: "physical", managed: false, status: "", used_by: [], locations: null }));
    const st = vi.spyOn(api, "fetchNetworkState").mockResolvedValue(state());
    routes("/networks/default/eno1?member=m2");
    expect(await screen.findByRole("heading", { name: "Network eno1 physical · on m2" })).toBeInTheDocument();
    expect(get).toHaveBeenCalledWith("default", "eno1", "m2");
    expect(screen.getByText("Not managed by LXD")).toBeInTheDocument();
    expect(screen.getByText("Nothing.")).toBeInTheDocument();
    expect(screen.queryByRole("toolbar")).toBeNull();
    expect(screen.queryByLabelText("Description")).toBeNull();
    await waitFor(() => expect(st).toHaveBeenCalledWith("default", "eno1", "m2"));
    expect(screen.queryByLabelText("On member")).toBeNull();
  });

  it("viewers can't edit; errors show", async () => {
    vi.spyOn(api, "fetchNetwork").mockResolvedValue(detail({ status: "", locations: [] }));
    vi.spyOn(api, "fetchNetworkState").mockRejectedValue(new Error("no state"));
    vi.spyOn(api, "fetchNetworkMembers").mockResolvedValue([{ member: "m1", config: { parent: "eno1" } }]);
    vi.spyOn(api, "fetchNetworkAllocations").mockRejectedValue(new Error("alloc down"));
    vi.spyOn(api, "fetchNetworkLeases").mockRejectedValue(new Error("Not implemented"));
    const a = routes("/networks/default/br0", viewer());
    expect(await screen.findByText("State unavailable: no state")).toBeInTheDocument();
    expect(screen.getByText("–")).toBeInTheDocument();
    expect(await screen.findByText("Leases unavailable: Not implemented")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("alloc down");
    expect(screen.queryByRole("toolbar")).toBeNull();
    expect(await screen.findByLabelText("m1")).toHaveAttribute("readonly");
    expect(screen.queryByRole("button", { name: "Save m1" })).toBeNull();
    a.unmount();
    vi.spyOn(api, "fetchNetwork").mockRejectedValue(new Error("Network not found"));
    vi.spyOn(api, "fetchNetworkAllocations").mockResolvedValue([]);
    vi.spyOn(api, "fetchNetworkLeases").mockResolvedValue([]);
    routes("/networks/default/gone");
    expect(await screen.findByRole("alert")).toHaveTextContent("Network not found");
  });

  it("empty allocations and leases", async () => {
    vi.spyOn(api, "fetchNetwork").mockResolvedValue(detail());
    vi.spyOn(api, "fetchNetworkState").mockResolvedValue(state());
    vi.spyOn(api, "fetchNetworkMembers").mockResolvedValue([]);
    vi.spyOn(api, "fetchNetworkAllocations").mockResolvedValue([]);
    vi.spyOn(api, "fetchNetworkLeases").mockResolvedValue([]);
    routes("/networks/default/br0");
    await waitFor(() => expect(screen.getAllByText("None.")).toHaveLength(2));
    expect(screen.queryByRole("heading", { name: "Per-member settings" })).toBeNull();
  });
});
