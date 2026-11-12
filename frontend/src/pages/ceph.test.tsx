import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { ratioSeries } from "../chart/panel";
import { renderAt } from "../test/render";
import { plots } from "../test/uplotMock";
import CephPage from "./CephPage";

const result = (name: string, series: api.Series[]): api.QueryResult => ({
  name,
  kind: "gauge",
  resolution: 0,
  fn: "value",
  start: 0,
  end: 1,
  series,
});

let data: Record<string, api.Series[]> = {};

beforeEach(() => {
  data = {};
  vi.spyOn(api, "queryMetric").mockImplementation((q) => Promise.resolve(result(q.name, data[q.name] ?? [])));
  vi.spyOn(api, "fetchMembers").mockResolvedValue([
    { name: "gw-1", url: "", status: "Online", roles: [], failure_domain: "", message: "", architecture: "" },
  ]);
  vi.spyOn(api, "fetchRbdTop").mockResolvedValue({ metric: "write_bytes", range: 3600, rows: [] });
});
afterEach(() => vi.restoreAllMocks());

const titles = () => plots.map((p) => p.opts.series.slice(1).map((s) => s.label));

const state: api.CephState = {
  enabled: true,
  ok: true,
  mgr: "http://192.0.2.1:9283",
  scraped_at: 1,
  health: { status: "HEALTH_WARN", checks: [{ name: "MON_DISK_LOW", severity: "HEALTH_WARN" }] },
  version: "19.2.3 squid",
  mons: [
    { name: "gw-1", in_quorum: true },
    { name: "gw-2", in_quorum: false },
  ],
  mgrs: [
    { name: "gw-1.x", active: true },
    { name: "gw-2.y", active: false },
  ],
  osds: [
    { id: 1, host: "gw-1", device_class: "ssd", up: true, in: true, pgs: 64, size: 100 * 1024 ** 3, used: 50 * 1024 ** 3, apply_latency_ms: 2, commit_latency_ms: 12 },
    { id: 2, host: "elsewhere", up: false, in: false },
    { id: 3 },
  ],
  pgs: { total: 193, states: { active: 193, clean: 190, degraded: 3 } },
  capacity: { total: 1000, used_raw: 500 },
  pools: [
    { id: 1, name: "lxd_remote", type: "replicated", description: "replica:3", stored: 2 * 1024 ** 3, max_avail: 1024 ** 3, percent_used: 0.684, objects: 12345, pgs: 128 },
    { id: 2 },
  ],
};

describe("CephPage", () => {
  it("shows health, OSDs, pools and history", async () => {
    vi.spyOn(api, "fetchCeph").mockResolvedValue(state);
    data = {
      ceph_pool_stored: [{ labels: { pool_id: "1" }, points: [[10, 5]] }, { labels: { pool_id: "9" }, points: [[10, 1]] }],
      ceph_osd_op_w_latency_sum: [
        { labels: { ceph_daemon: "osd.1" }, points: [[10, 0.5], [20, 0.2]] },
        { labels: { ceph_daemon: "osd.9" }, points: [[10, 1]] },
      ],
      ceph_osd_op_w_latency_count: [{ labels: { ceph_daemon: "osd.1" }, points: [[10, 100], [20, 0]] }],
      ceph_cluster_total_bytes: [{ labels: {}, points: [[10, 9]] }],
      ceph_cluster_total_used_raw_bytes: [{ labels: {}, points: [[10, 3]] }],
      ceph_pool_rd_bytes: [{ labels: { pool_id: "1" }, points: [[10, 1]] }],
      ceph_pool_wr_bytes: [{ labels: { pool_id: "1" }, points: [[10, 1]] }],
      ceph_pool_rd: [{ labels: { pool_id: "1" }, points: [[10, 1]] }],
      ceph_pool_wr: [{ labels: { pool_id: "1" }, points: [[10, 1]] }],
      ceph_osd_apply_latency_ms: [{ labels: { ceph_daemon: "osd.1" }, points: [[10, 3]] }],
      ceph_osd_commit_latency_ms: [{ labels: { ceph_daemon: "osd.1" }, points: [[10, 4]] }],
      ceph_osd_op_r_latency_sum: [{ labels: { ceph_daemon: "osd.1" }, points: [[10, 0.1]] }],
      ceph_osd_op_r_latency_count: [{ labels: { ceph_daemon: "osd.1" }, points: [[10, 50]] }],
    };
    renderAt(<CephPage />);
    expect(await screen.findByRole("status")).toHaveTextContent("HEALTH_WARN · Ceph 19.2.3 squid · from http://192.0.2.1:9283");
    expect(screen.getByRole("list", { name: "Health checks" })).toHaveTextContent("MON_DISK_LOW HEALTH_WARN");
    expect(screen.getByText(/1 \/ 2/)).toHaveTextContent("(out: gw-2)");
    expect(screen.getByText("gw-1.x")).toBeInTheDocument();
    expect(screen.getByText("1 up, 1 in, of 3")).toBeInTheDocument();
    expect(screen.getByText("193: 193 active, 190 clean, 3 degraded")).toBeInTheDocument();
    expect(screen.getByRole("meter", { name: "Raw used" })).toHaveAttribute("value", "50");
    const osd1 = screen.getByText("osd.1").closest("tr")!;
    expect(await within(osd1).findByRole("link", { name: "gw-1" })).toHaveAttribute("href", "/members/gw-1");
    expect(osd1).toHaveTextContent("up, in");
    expect(osd1).toHaveTextContent("2.0 ms");
    expect(osd1).toHaveTextContent("12 ms");
    const osd2 = screen.getByText("osd.2").closest("tr")!;
    expect(osd2).toHaveTextContent("elsewhere");
    expect(within(osd2).getByRole("alert")).toHaveTextContent("down");
    expect(osd2).toHaveTextContent("out");
    expect(screen.getByText("osd.3").closest("tr")).toHaveTextContent("osd.3––");
    const pool = screen.getByText("lxd_remote").closest("tr")!;
    expect(pool).toHaveTextContent("replicated replica:3");
    expect(pool).toHaveTextContent("68.4%");
    expect(pool).toHaveTextContent("12.3k");
    expect(screen.getByText("pool 2").closest("tr")).toHaveTextContent("pool 2 –––––");
    expect(screen.getAllByRole("figure")).toHaveLength(8);
    await vi.waitFor(() => expect(plots.length).toBe(8));
    expect(titles()).toContainEqual(["lxd_remote", "pool 9"]);
    expect(titles()).toContainEqual(["used (raw)", "total (raw)"]);
    expect(titles()).toContainEqual(["lxd_remote read", "lxd_remote write"]);
    // per-op latency: osd.9 has no count series, so only osd.1; 0.5 / 100 s = 5 ms, zero-count point dropped
    const lat = plots.filter((p) => p.opts.series[1]?.label === "osd.1");
    expect(lat.map((p) => p.data[1])).toContainEqual([5]);
    expect(lat.map((p) => p.data[1])).toContainEqual([2]); // read: 0.1 / 50 s
  });

  it("hides OSD size and per-op latency when the mgr leaves out perf counters (Tentacle)", async () => {
    vi.spyOn(api, "fetchCeph").mockResolvedValue({ ...state, perf_counters: false });
    renderAt(<CephPage />);
    expect(await screen.findByText(/doesn't export OSD performance counters/)).toHaveTextContent(
      "sudo ceph config set mgr mgr/prometheus/exclude_perf_counters false",
    );
    const osdTable = screen.getAllByRole("table")[0];
    expect(within(osdTable).queryByRole("columnheader", { name: "Size" })).toBeNull();
    expect(within(osdTable).getByRole("columnheader", { name: "Apply" })).toBeInTheDocument();
    expect(screen.getByText("OSD apply latency")).toBeInTheDocument();
    expect(screen.queryByText("OSD read latency (per op)")).toBeNull();
    expect(screen.queryByText("OSD write latency (per op)")).toBeNull();
  });

  it("lists the busiest RBD images with their owners", async () => {
    vi.spyOn(api, "fetchCeph").mockResolvedValue({ enabled: true, ok: true, pools: [] });
    const top = vi.spyOn(api, "fetchRbdTop").mockResolvedValue({
      metric: "write_bytes",
      range: 3600,
      rows: [
        { pool: "lxd_remote", image: "container_web", value: 2048, owner: { kind: "instance", type: "container", project: "default", name: "web" } },
        { pool: "lxd_remote", image: "virtual-machine_lab_vm1.block", value: 1024, owner: { kind: "instance", type: "virtual-machine", project: "lab", name: "vm1", block: true } },
        { pool: "lxd_remote", image: "virtual-machine_vm2", value: 10, owner: { kind: "instance", type: "virtual-machine", project: "default", name: "vm2", block: false } },
        { pool: "lxd_remote", image: "custom_lab_data", value: 512, owner: { kind: "volume", type: "custom", project: "lab", name: "data" } },
        { pool: "lxd_remote", image: "image_abcdef0123456789_ext4", value: 5, owner: { kind: "image", name: "abcdef0123456789" } },
        { pool: "lxd_remote", image: "zombie_container_old", value: 1, owner: { kind: "instance", name: "old", deleted: true } },
        { pool: "other", image: "lxd_lxd_remote", value: 0, owner: { kind: "other" } },
      ],
    });
    renderAt(<CephPage />);
    const section = within(await screen.findByRole("region", { name: "Busiest disks" }));
    expect(await section.findByRole("link", { name: "web" })).toHaveAttribute("href", "/instances/default/web");
    expect(section.getByText("web").closest("tr")).toHaveTextContent("container");
    expect(section.getByRole("link", { name: "lab/vm1" }).closest("tr")).toHaveTextContent("VM disk");
    expect(section.getByRole("link", { name: "vm2" }).closest("tr")).toHaveTextContent("VM config");
    expect(section.getByRole("link", { name: "data" })).toHaveAttribute("href", "/storage/volumes/lab/remote/data");
    expect(section.getByText("image abcdef012345")).toBeInTheDocument();
    expect(section.getByText("zombie_container_old")).toBeInTheDocument();
    expect(section.getByText("lxd_lxd_remote")).toBeInTheDocument();
    expect(section.getByText("2.0 KiB/s")).toBeInTheDocument();
    fireEvent.change(section.getByLabelText("Show"), { target: { value: "write_latency" } });
    await vi.waitFor(() => expect(top).toHaveBeenLastCalledWith("write_latency", 3600));
    expect(await section.findByText("2048 ms")).toBeInTheDocument();
    fireEvent.change(section.getByLabelText("Show"), { target: { value: "read_ops" } });
    expect(await section.findByText("2.0k/s")).toBeInTheDocument();
  });

  it("explains when Ceph is off, unreachable or empty", async () => {
    vi.spyOn(api, "fetchCeph").mockResolvedValue({ enabled: false });
    const a = renderAt(<CephPage />);
    expect(await screen.findByText(/Ceph metrics are turned off/)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "History" })).toBeNull();
    a.unmount();
    vi.spyOn(api, "fetchCeph").mockResolvedValue({ enabled: true, ok: false, error: "no active ceph-mgr found" });
    vi.spyOn(api, "fetchRbdTop").mockRejectedValue(new Error("history off"));
    const b = renderAt(<CephPage />);
    expect(await screen.findByText("No data from ceph-mgr: no active ceph-mgr found")).toBeInTheDocument();
    expect(await screen.findByText("history off")).toBeInTheDocument();
    b.unmount();
    vi.spyOn(api, "fetchCeph").mockResolvedValue({ enabled: true, ok: true });
    vi.spyOn(api, "fetchRbdTop").mockResolvedValue({ metric: "write_bytes", range: 3600, rows: [] });
    const c = renderAt(<CephPage />);
    expect(await screen.findByText("No RBD image data in this range.")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("UNKNOWN");
    expect(screen.getByText("0 / 0")).toBeInTheDocument();
    expect(screen.getByText("0 up, 0 in, of 0")).toBeInTheDocument();
    expect(screen.getByText("0: –")).toBeInTheDocument();
    c.unmount();
    vi.spyOn(api, "fetchCeph").mockRejectedValue(new Error("backend down"));
    renderAt(<CephPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("backend down");
  });
});

describe("ratioSeries", () => {
  it("divides matching series point by point", () => {
    const out = ratioSeries("k", 2, " w")([
      result("sum", [
        { labels: { k: "a" }, points: [[1, 4], [2, 6], [3, 1]] },
        { labels: { k: "b" }, points: [[1, 1]] },
      ]),
      result("count", [{ labels: { k: "a" }, points: [[1, 2], [2, 0]] }]),
    ]);
    expect(out).toEqual([{ label: "a w", points: [[1, 4]] }]);
  });
});
