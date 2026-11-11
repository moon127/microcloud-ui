import { screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { cpuModeSeries, usedPercentSeries } from "../chart/panel";
import { renderAt } from "../test/render";
import { plots } from "../test/uplotMock";
import HostsSection from "./Hosts";
import { hostMemory } from "./hostValues";
import MemberPage from "./MemberPage";

const GIB = 1024 ** 3;

const result = (name: string, series: api.Series[]): api.QueryResult => ({
  name,
  kind: "gauge",
  resolution: 0,
  fn: "value",
  start: 0,
  end: 1,
  series,
});

const node = (over: Partial<api.NodeNow> = {}): api.NodeNow => ({
  url: "https://192.0.2.1:9100",
  ok: true,
  error: null,
  absent: false,
  memory_total: 32 * GIB,
  memory_used: 8 * GIB,
  swap_total: 4 * GIB,
  swap_used: GIB,
  rootfs_size: 100,
  rootfs_used: 90,
  cpu_percent: 42,
  cpu_iowait_percent: 7,
  cpu_steal_percent: 0,
  disk_busy_percent: 55,
  disk_busiest: "sda",
  ...over,
});

const host = (over: Partial<api.Host> = {}): api.Host => ({
  name: "gw-1",
  status: "Online",
  roles: [],
  cpus: 8,
  load1: 2,
  load5: 1,
  uptime: 100,
  memory_total: 32 * GIB,
  memory_free: 8 * GIB,
  memory_buffered: 2 * GIB,
  swap_total: 0,
  swap_free: 0,
  instances: 5,
  instances_running: 4,
  instances_cpu: 1.5,
  instances_memory: 6 * GIB,
  allocated_cpus: 12,
  allocated_memory: 20 * GIB,
  unlimited: 2,
  net_rx_bps: 1024,
  net_tx_bps: 2048,
  node: node(),
  ...over,
});

afterEach(() => vi.restoreAllMocks());

describe("HostsSection", () => {
  it("prefers node_exporter values and falls back to LXD's", async () => {
    vi.spyOn(api, "fetchHosts").mockResolvedValue({
      node_exporter: true,
      hosts: [
        host(),
        host({ name: "gw-2", node: { url: "", ok: false, error: "not reachable (not installed?)", absent: true }, allocated_cpus: 4 }),
        host({ name: "gw-3", status: "Offline", node: { url: "", ok: false, error: "HTTP 401: check username/password", absent: false }, cpus: null, load1: null, memory_free: null, unlimited: 0, net_rx_bps: null }),
      ],
    });
    renderAt(<HostsSection />);
    const row = (await screen.findByRole("link", { name: "gw-1" })).closest("tr")!;
    expect(within(row).getByRole("meter", { name: "gw-1 CPU" })).toHaveAttribute("value", "42");
    expect(row).toHaveTextContent("42.0% (iowait 7.0%)");
    expect(row).toHaveTextContent("8.0 GiB / 32.0 GiB");
    expect(row).not.toHaveTextContent("≈");
    expect(row).toHaveTextContent("1.0 GiB / 4.0 GiB");
    expect(within(row).getByRole("meter", { name: "gw-1 root filesystem" })).toHaveAttribute("value", "90");
    expect(row).toHaveTextContent("sda 55.0% busy");
    expect(row).toHaveTextContent("4 / 5");
    expect(row).toHaveTextContent("1.50 CPUs, 6.0 GiB");
    expect(row).toHaveTextContent("12 vCPU / 8 (1.5×), 20.0 GiB / 32.0 GiB + 2 unlimited");
    expect(row).toHaveTextContent("1.0 KiB/s / 2.0 KiB/s");

    const gw2 = screen.getByRole("link", { name: "gw-2" }).closest("tr")!;
    expect(within(gw2).getByRole("meter", { name: "gw-2 load per CPU" })).toHaveAttribute("value", "25");
    expect(gw2).toHaveTextContent("load 2 / 8");
    expect(gw2).toHaveTextContent("≈22.0 GiB / 32.0 GiB"); // 32 - 8 free - 2 buffers
    expect(gw2).toHaveTextContent("none"); // no swap
    expect(gw2).toHaveTextContent("4 vCPU / 8,");
    expect(gw2).not.toHaveTextContent("×");

    const gw3 = screen.getByRole("link", { name: "gw-3" }).closest("tr")!;
    expect(within(gw3).getByRole("alert")).toHaveTextContent("Offline");
    expect(gw3).toHaveTextContent("12 vCPU, 20.0 GiB / 32.0 GiB");
    expect(screen.getByText("node_exporter on gw-3: HTTP 401: check username/password")).toHaveAttribute("role", "alert");
    expect(screen.getByText(/No node_exporter on gw-2:/)).toHaveTextContent("docs/HOST-METRICS.md");
  });

  it("node_exporter quirks, off, loading and errors", async () => {
    vi.spyOn(api, "fetchHosts").mockResolvedValue({
      node_exporter: false,
      hosts: [
        host({ node: node({ cpu_percent: 10, cpu_iowait_percent: 0, cpu_steal_percent: 12, rootfs_size: undefined, disk_busy_percent: undefined, swap_total: 0 }) }),
        host({ name: "gw-2", node: node({ cpu_percent: undefined }), load1: null, swap_total: 2 * GIB, swap_free: GIB, memory_total: null, allocated_memory: 0 }),
      ],
    });
    const a = renderAt(<HostsSection />);
    const row = (await screen.findByRole("link", { name: "gw-1" })).closest("tr")!;
    expect(row).toHaveTextContent("10.0% (steal 12.0%)");
    expect(row.querySelectorAll("td")[4]).toHaveTextContent(/^$/);
    expect(row.querySelectorAll("td")[3]).toHaveTextContent("none");
    const gw2 = screen.getByRole("link", { name: "gw-2" }).closest("tr")!;
    expect(gw2.querySelectorAll("td")[1]).toHaveTextContent("–"); // no CPU % yet, no load
    expect(gw2.querySelectorAll("td")[3]).toHaveTextContent("1.0 GiB / 4.0 GiB"); // node swap wins
    expect(gw2.querySelectorAll("td")[7]).toHaveTextContent("12 vCPU / 8 (1.5×), 0 B + 2 unlimited");
    expect(screen.getByText(/node_exporter are turned off/)).toBeInTheDocument();
    a.unmount();
    vi.spyOn(api, "fetchHosts").mockReturnValue(new Promise(() => {}));
    const b = renderAt(<HostsSection />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading hosts…");
    b.unmount();
    vi.spyOn(api, "fetchHosts").mockRejectedValue(new Error("LXD is not configured"));
    renderAt(<HostsSection />);
    expect(await screen.findByRole("alert")).toHaveTextContent("LXD is not configured");
  });

  it("hostMemory without any data", () => {
    expect(hostMemory(host({ node: null, memory_total: null }))).toBeNull();
    expect(hostMemory(host({ node: null, memory_buffered: null }))).toEqual({ used: 24 * GIB, total: 32 * GIB, estimated: true });
  });
});

describe("MemberPage with node_exporter", () => {
  let data: Record<string, api.Series[]> = {};
  beforeEach(() => {
    data = {};
    vi.spyOn(api, "queryMetric").mockImplementation((q) => Promise.resolve(result(q.name, data[q.name] ?? [])));
    vi.spyOn(api, "fetchMemberResources").mockResolvedValue({ cpu: { total: 2 }, memory: { total: 1 } });
    vi.spyOn(api, "fetchMembers").mockResolvedValue([]);
  });

  it("shows host graphs and hides LXD's coarse memory", async () => {
    vi.spyOn(api, "fetchHosts").mockResolvedValue({ node_exporter: true, hosts: [host({ name: "m1" })] });
    const m = { member: "m1" };
    data = {
      node_cpu_seconds_total: [
        { labels: { mode: "idle" }, points: [[10, 6]] },
        { labels: { mode: "user" }, points: [[10, 2]] },
        { labels: { mode: "softirq" }, points: [[10, 1]] },
        { labels: { mode: "nice" }, points: [[10, 1]] },
      ],
      node_memory_MemTotal_bytes: [{ labels: m, points: [[10, 100]] }],
      node_memory_MemAvailable_bytes: [{ labels: m, points: [[10, 40]] }],
      node_memory_Cached_bytes: [{ labels: m, points: [[10, 20]] }],
      node_memory_Buffers_bytes: [{ labels: m, points: [[10, 5]] }],
      node_memory_SwapTotal_bytes: [{ labels: m, points: [[10, 10]] }],
      node_memory_SwapFree_bytes: [{ labels: m, points: [[10, 7]] }],
      node_zfs_arc_size: [{ labels: m, points: [[10, 2 ** 20]] }],
      node_filesystem_size_bytes: [{ labels: { mountpoint: "/" }, points: [[10, 200]] }],
      node_filesystem_avail_bytes: [{ labels: { mountpoint: "/" }, points: [[10, 50]] }],
      node_disk_read_bytes_total: [{ labels: { device: "sda" }, points: [[10, 3]] }],
      node_disk_io_time_seconds_total: [{ labels: { device: "sda" }, points: [[10, 0.25]] }],
      node_pressure_io_waiting_seconds_total: [{ labels: m, points: [[10, 0.1]] }],
    };
    plots.length = 0;
    renderAt(<MemberPage />, { url: "/members/m1", path: "/members/:name" });
    expect(await screen.findByRole("heading", { name: "Host (node_exporter)" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "LXD" })).toBeInTheDocument();
    expect(screen.queryByRole("figure", { name: "Host memory" })).not.toBeNull();
    expect(screen.getAllByRole("figure", { name: "Host memory" })).toHaveLength(1);
    await vi.waitFor(() => expect(plots.find((p) => p.opts.series[1]?.label === "user")).toBeDefined());
    const cpu = plots.find((p) => p.opts.series[1]?.label === "user")!;
    expect(cpu.opts.series.slice(1).map((s) => s.label)).toEqual(["user", "other"]);
    expect(cpu.data).toEqual([[10], [20], [20]]);
    await vi.waitFor(() => expect(plots.find((p) => p.opts.series[1]?.label === "used")).toBeDefined());
    const mem = plots.find((p) => p.opts.series[1]?.label === "used")!;
    expect(mem.opts.series.slice(1).map((s) => s.label)).toEqual(["used", "cache + buffers", "total", "swap used", "ZFS ARC"]);
    expect(mem.data).toEqual([[10], [60], [25], [100], [3], [2 ** 20]]);
    await vi.waitFor(() => expect(plots.find((p) => p.opts.series[1]?.label === "/")).toBeDefined());
    expect(plots.find((p) => p.opts.series[1]?.label === "/")!.data).toEqual([[10], [75]]);
    await vi.waitFor(() => expect(plots.find((p) => p.opts.series[1]?.label === "sda")).toBeDefined());
    expect(plots.find((p) => p.opts.series[1]?.label === "sda")!.data).toEqual([[10], [25]]);
    await vi.waitFor(() => expect(plots.find((p) => p.opts.series[3]?.label === "io")).toBeDefined());
    expect(plots.find((p) => p.opts.series[3]?.label === "io")!.data[3]).toEqual([10]);
  });

  it("explains a missing or failing node_exporter", async () => {
    vi.spyOn(api, "fetchHosts").mockResolvedValue({
      node_exporter: true,
      hosts: [host({ name: "m1", node: { url: "", ok: false, error: "x", absent: true } }), host({ name: "m2", node: { url: "", ok: false, error: "HTTP 401", absent: false } })],
    });
    const a = renderAt(<MemberPage />, { url: "/members/m1", path: "/members/:name" });
    expect(await screen.findByText(/No node_exporter on this host/)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "LXD" })).toBeNull();
    expect(screen.getByRole("figure", { name: "Host memory" })).toBeInTheDocument();
    a.unmount();
    renderAt(<MemberPage />, { url: "/members/m2", path: "/members/:name" });
    expect(await screen.findByText("node_exporter: HTTP 401")).toHaveAttribute("role", "alert");
  });
});

describe("host chart helpers", () => {
  it("cpu modes as percentages", () => {
    const out = cpuModeSeries([
      result("cpu", [
        { labels: { mode: "idle" }, points: [[1, 5], [2, 0]] },
        { labels: { mode: "system" }, points: [[1, 5]] },
        { labels: { mode: "iowait" }, points: [[2, 0]] },
      ]),
    ]);
    expect(out).toEqual([
      { label: "system", points: [[1, 50]] },
      { label: "iowait", points: [] },
    ]);
  });

  it("used percent per label, skipping unmatched and empty", () => {
    const out = usedPercentSeries("mountpoint")([
      result("size", [
        { labels: { mountpoint: "/" }, points: [[1, 100], [2, 0]] },
        { labels: { mountpoint: "/boot" }, points: [[1, 10]] },
      ]),
      result("avail", [{ labels: { mountpoint: "/" }, points: [[1, 40], [2, 0]] }]),
    ]);
    expect(out).toEqual([{ label: "/", points: [[1, 60]] }]);
  });
});
