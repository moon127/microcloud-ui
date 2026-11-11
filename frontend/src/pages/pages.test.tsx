import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { renderAt } from "../test/render";
import { plots } from "../test/uplotMock";
import InstancePage from "./instance/InstancePage";
import InstancesPage from "./InstancesPage";
import MemberPage from "./MemberPage";
import NetworksPage from "./NetworksPage";
import StoragePage from "./StoragePage";

const result = (name: string, series: api.Series[]): api.QueryResult => ({
  name,
  kind: "gauge",
  resolution: 0,
  fn: "value",
  start: 0,
  end: 1,
  series,
});

/** Series returned per metric name; anything else is empty. */
let data: Record<string, api.Series[]> = {};
/** Returned for metrics not in ``data``; tests set it to exercise every panel. */
let fallback: api.Series[] = [];
const everything: api.Series[] = [
  {
    labels: { member: "m1", network: "n", device: "d", result: "ok", pool_id: "1", pool: "p", mountpoint: "/" },
    points: [[10, 1]],
  },
];

beforeEach(() => {
  data = {};
  fallback = [];
  vi.spyOn(api, "queryMetric").mockImplementation((q) => Promise.resolve(result(q.name, data[q.name] ?? fallback)));
});
afterEach(() => vi.restoreAllMocks());

const titles = () => plots.map((p) => p.opts.series.slice(1).map((s) => s.label));
const chart = (name: string) => within(screen.getByRole("figure", { name }));

const inst: api.Instance = {
  project: "default",
  name: "c1",
  type: "virtual-machine",
  status: "Running",
  location: "m1",
  description: "web box",
  nics: { enp5s0: "ovn1", eth1: "ovn1" },
};

const full = (over: Partial<api.InstanceFull> = {}): api.InstanceFull => ({
  ...inst,
  profiles: ["default"],
  config: {},
  devices: {},
  ...over,
});

describe("InstancesPage", () => {
  it("lists and filters instances", async () => {
    vi.spyOn(api, "fetchInstances").mockResolvedValue([
      inst,
      { ...inst, project: "lab", name: "c2", type: "container", location: "", nics: {}, description: "" },
    ]);
    renderAt(<InstancesPage />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading");
    const row = (await screen.findByText("web box")).closest("tr")!;
    expect(within(row).getByRole("link", { name: "c1" })).toHaveAttribute("href", "/instances/default/c1");
    expect(within(row).getByRole("link", { name: "m1" })).toHaveAttribute("href", "/members/m1");
    expect(row).toHaveTextContent("VM");
    expect(row).toHaveTextContent("ovn1");
    expect(screen.getByRole("link", { name: "lab/c2" }).closest("tr")).toHaveTextContent("container");
    fireEvent.change(screen.getByLabelText("Filter instances"), { target: { value: "LAB" } });
    expect(screen.queryByText("web box")).toBeNull();
    fireEvent.change(screen.getByLabelText("Filter instances"), { target: { value: "zzz" } });
    expect(screen.getByText("No instances.")).toBeInTheDocument();
  });

  it("shows errors", async () => {
    vi.spyOn(api, "fetchInstances").mockRejectedValue(new Error("down"));
    renderAt(<InstancesPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("down");
  });
});

describe("InstancePage", () => {
  it("draws the instance graphs", async () => {
    vi.spyOn(api, "fetchInstance").mockResolvedValue(full());
    data = {
      mcui_instance_cpu_busy_seconds_total: [{ labels: { name: "c1" }, points: [[10, 0.5]] }],
      mcui_instance_cpus: [{ labels: { name: "c1" }, points: [[10, 2]] }],
      mcui_instance_memory_used_bytes: [{ labels: { name: "c1" }, points: [[10, 5]] }],
      lxd_disk_read_bytes_total: [{ labels: { device: "sda" }, points: [[10, 1]] }],
      lxd_filesystem_size_bytes: [{ labels: { device: "sda1", mountpoint: "/" }, points: [[10, 100]] }],
      lxd_filesystem_avail_bytes: [{ labels: { device: "sda1", mountpoint: "/" }, points: [[10, 40]] }],
    };
    renderAt(<InstancePage />, { url: "/instances/default/c1?range=900", path: "/instances/:project/:name" });
    expect(await screen.findByRole("link", { name: "m1" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("c1 VM · Running · on m1");
    await vi.waitFor(() => expect(plots.length).toBe(4));
    const cpu = plots.find((p) => p.opts.series[1].label === "CPU")!;
    expect(cpu.data).toEqual([[10], [25]]);
    const fs = plots.find((p) => p.opts.series[1].label === "/")!;
    expect(fs.data).toEqual([[10], [60]]);
    expect(titles()).toContainEqual(["sda read"]);
    expect(titles()).toContainEqual(["used"]);
    expect(api.queryMetric).toHaveBeenCalledWith(
      expect.objectContaining({ name: "lxd_procs_total", match: { project: "default", name: "c1" }, range: 900 }),
    );
    expect(chart("Processes").getByText("No data yet for this range.")).toBeInTheDocument();
  });

  it("handles a missing CPU count and a project prefix", async () => {
    vi.spyOn(api, "fetchInstance").mockRejectedValue(new Error("no such instance"));
    data = { mcui_instance_cpu_busy_seconds_total: [{ labels: { name: "c" }, points: [[10, 1]] }] };
    renderAt(<InstancePage />, { url: "/instances/lab/c", path: "/instances/:project/:name" });
    expect(await screen.findByRole("alert")).toHaveTextContent("no such instance");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("lab/c");
    await vi.waitFor(() => expect(chart("CPU (% of its CPUs)").getByText("No data yet for this range.")).toBeInTheDocument());
  });
});

const resources: api.Resources = {
  cpu: { total: 8, sockets: [{ name: "Xeon D-1518" }] },
  memory: { total: 64 * 1024 ** 3 },
  gpu: { total: 1, cards: [{ vendor: "ASPEED", product: "AST2400" }] },
  network: {
    cards: [
      { product: "X552", ports: [{ id: "eno7", link_detected: true, link_speed: 10000 }, { id: "eno1", link_detected: false }] },
      { driver: "virtio", ports: [{ id: "x", link_detected: true }] },
      {},
    ],
  },
  storage: { disks: [{ id: "nvme0n1", model: "Samsung", type: "nvme", size: 1e12 }, { id: "sda", size: 5e11 }] },
  system: { vendor: "Supermicro", product: "X10SDV" },
};

describe("MemberPage", () => {
  it("shows hardware and member graphs", async () => {
    vi.spyOn(api, "fetchMemberResources").mockResolvedValue(resources);
    data = {
      mcui_member_memory_total_bytes: [{ labels: { member: "m1" }, points: [[10, 100]] }],
      mcui_member_memory_free_bytes: [{ labels: { member: "m1" }, points: [[10, 30]] }],
      mcui_member_memory_buffered_bytes: [{ labels: { member: "m1" }, points: [[10, 10]] }],
      mcui_host_network_receive_bytes_total: [
        { labels: { member: "m1", network: "bond0" }, points: [[10, 5]] },
        { labels: { member: "m1", network: "eno1" }, points: [[10, 0]] },
      ],
      lxd_api_requests_completed_total: [{ labels: { result: "succeeded" }, points: [[10, 2]] }],
    };
    renderAt(<MemberPage />, { url: "/members/m1", path: "/members/:name" });
    const hw = within(await screen.findByRole("region", { name: "Hardware" }));
    expect(hw.getByText("Supermicro X10SDV")).toBeInTheDocument();
    expect(hw.getByText("Xeon D-1518 (1 socket, 8 threads)")).toBeInTheDocument();
    expect(hw.getByText("64.0 GiB")).toBeInTheDocument();
    expect(hw.getByText("ASPEED AST2400")).toBeInTheDocument();
    expect(hw.getByText("X552 (eno7 10G, eno1 down); virtio (x up); NIC")).toBeInTheDocument();
    expect(hw.getByText("nvme0n1: Samsung nvme 931 GiB; sda: disk 466 GiB")).toBeInTheDocument();
    await vi.waitFor(() => expect(plots.length).toBe(3));
    const mem = plots.find((p) => p.opts.series[1].label === "used (excl. buffers)")!;
    expect(mem.data).toEqual([[10], [60], [100]]);
    expect(titles()).toContainEqual(["bond0 rx"]); // eno1 is all zero: hidden
    expect(titles()).toContainEqual(["succeeded"]);
  });

  it("shows minimal hardware and errors", async () => {
    vi.spyOn(api, "fetchMemberResources").mockResolvedValue({ cpu: { total: 4, architecture: "aarch64", sockets: [{}, {}] }, memory: { total: 1 } });
    renderAt(<MemberPage />, { url: "/members/m2", path: "/members/:name" });
    const hw = within(await screen.findByRole("region", { name: "Hardware" }));
    expect(hw.getByText("aarch64 (2 sockets, 4 threads)")).toBeInTheDocument();
    expect(hw.getAllByText("none")).toHaveLength(3);
    expect(screen.queryByText("System")).toBeNull();
  });

  it("falls back when the CPU has no sockets listed", async () => {
    vi.spyOn(api, "fetchMemberResources").mockResolvedValue({ cpu: { total: 2 }, memory: { total: 1 } });
    renderAt(<MemberPage />, { url: "/members/m3", path: "/members/:name" });
    expect(await screen.findByText("– (1 socket, 2 threads)")).toBeInTheDocument();
  });

  it("reports resource errors", async () => {
    vi.spyOn(api, "fetchMemberResources").mockRejectedValue(new Error("no such member"));
    renderAt(<MemberPage />, { url: "/members/x", path: "/members/:name" });
    expect(await screen.findByRole("alert")).toHaveTextContent("no such member");
  });
});

describe("StoragePage", () => {
  beforeEach(() => {
    vi.spyOn(api, "fetchStoragePools").mockResolvedValue([]);
  });

  it("draws LXD pools and Ceph pools", async () => {
    data = {
      mcui_pool_space_total: [{ labels: { pool: "remote", member: "m1" }, points: [[10, 100]] }],
      mcui_pool_space_used: [{ labels: { pool: "remote", member: "m1" }, points: [[10, 60]] }],
      ceph_pool_metadata: [{ labels: { pool_id: "1", name: "lxd_remote" }, points: [[10, 1]] }],
      ceph_pool_stored: [
        { labels: { pool_id: "1" }, points: [[10, 5]] },
        { labels: { pool_id: "9" }, points: [[10, 1]] },
      ],
    };
    renderAt(<StoragePage />);
    await vi.waitFor(() => expect(plots.length).toBe(1));
    expect(screen.getByRole("figure", { name: "remote: space used" })).toBeInTheDocument();
    expect(titles()).toContainEqual(["m1 used", "size"]);
    expect(screen.getByRole("link", { name: "Ceph" })).toHaveAttribute("href", "/ceph");
  });

  it("explains when nothing is collected yet", async () => {
    renderAt(<StoragePage />);
    expect(await screen.findByText("No pool data collected yet.")).toBeInTheDocument();
  });

  it("shows errors", async () => {
    vi.spyOn(api, "queryMetric").mockRejectedValue(new Error("no history"));
    renderAt(<StoragePage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("no history");
  });
});

describe("NetworksPage", () => {
  beforeEach(() => {
    vi.spyOn(api, "fetchNetworks").mockResolvedValue([]);
    vi.spyOn(api, "fetchMembers").mockResolvedValue([]);
  });

  it("draws per-network and host traffic", async () => {
    vi.spyOn(api, "fetchNetworkTraffic").mockResolvedValue({
      start: 0,
      end: 1,
      resolution: 0,
      rx: [{ labels: { network: "ovn1" }, points: [[10, 1]] }],
      tx: [{ labels: { network: "ovn1" }, points: [[10, 2]] }],
    });
    data = {
      mcui_host_network_receive_bytes_total: [{ labels: { member: "m1", network: "bond0" }, points: [[10, 4]] }],
      mcui_host_network_transmit_bytes_total: [{ labels: { member: "m1", network: "bond0" }, points: [[10, 3]] }],
    };
    renderAt(<NetworksPage />, { url: "/networks?range=86400" });
    await vi.waitFor(() => expect(plots.length).toBe(3));
    expect(titles()).toEqual(expect.arrayContaining([["ovn1 rx"], ["ovn1 tx"], ["m1 bond0 rx", "m1 bond0 tx"]]));
    expect(api.fetchNetworkTraffic).toHaveBeenCalledWith(86400);
  });

  it("shows errors", async () => {
    vi.spyOn(api, "fetchNetworkTraffic").mockRejectedValue(new Error("nope"));
    renderAt(<NetworksPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("nope");
  });
});

describe("every panel with data", () => {
  it("instance page", async () => {
    fallback = everything;
    vi.spyOn(api, "fetchInstance").mockResolvedValue(full({ type: "container" }));
    data = { lxd_filesystem_avail_bytes: [] };
    renderAt(<InstancePage />, { url: "/instances/default/c1", path: "/instances/:project/:name" });
    await vi.waitFor(() => expect(plots.length).toBe(9));
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("container");
    // no "avail" series: used space can't be computed
    expect(chart("Filesystem used").getByText("No data yet for this range.")).toBeInTheDocument();
    expect(titles()).toContainEqual(["d rx errors", "d tx errors", "d rx drops", "d tx drops"]);
  });

  it("member page", async () => {
    fallback = everything;
    vi.spyOn(api, "fetchMemberResources").mockResolvedValue(resources);
    renderAt(<MemberPage />, { url: "/members/m1", path: "/members/:name" });
    await vi.waitFor(() => expect(plots.length).toBe(7));
    expect(titles()).toContainEqual(["goroutines", "ongoing requests", "warnings"]);
  });

  it("storage page", async () => {
    fallback = everything;
    vi.spyOn(api, "fetchStoragePools").mockResolvedValue([]);
    renderAt(<StoragePage />);
    await vi.waitFor(() => expect(plots.length).toBe(1));
  });
});
