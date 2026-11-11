import { act, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { renderAt, session } from "../test/render";
import Overview from "./Overview";

const server: api.ServerInfo = {
  version: "5.21.8",
  clustered: true,
  server_name: "m1",
  auth: "trusted",
  auth_method: "tls",
  endpoint: "https://192.0.2.1:8443",
  features: { metrics: true, storage_buckets: false, network_peers: false },
};

function instance(over: Partial<api.InstanceLive>): api.InstanceLive {
  return {
    member: "m1",
    project: "default",
    name: "c1",
    type: "container",
    cpu_percent: null,
    cpus: 2,
    memory_total: null,
    memory_used: null,
    memory_rss: null,
    memory_cached: null,
    memory_swap: null,
    oom_kills: null,
    disk_read_bps: null,
    disk_write_bps: null,
    disk_read_iops: null,
    disk_write_iops: null,
    net_rx_bps: null,
    net_tx_bps: null,
    procs: null,
    rootfs_size: null,
    rootfs_used: null,
    ...over,
  };
}

const member = (over: Partial<api.MemberLive>): api.MemberLive => ({
  name: "m1",
  url: "https://192.0.2.1:8443",
  status: "Online",
  roles: ["database-leader", "database"],
  failure_domain: "default",
  message: "",
  architecture: "x86_64",
  scrape: { member: "m1", url: "", port: 8443, ok: true, error: null, fell_back: false, samples: 10 },
  daemon: { uptime_seconds: 90061, goroutines: 321, warnings: 3 },
  instances: 2,
  ...over,
});

const live: api.LiveMetrics = {
  scraped_at: 1700000000,
  interval_seconds: 15,
  members: [
    member({}),
    member({
      name: "m2",
      scrape: { member: "m2", url: "", port: 8443, ok: false, error: "HTTP 403", fell_back: false, samples: 0 },
      daemon: {},
      instances: 0,
    }),
    member({
      name: "m3",
      scrape: { member: "m3", url: "", port: 8443, ok: true, error: null, fell_back: true, samples: 1 },
    }),
  ],
  instances: [
    instance({ name: "idle", cpu_percent: null }),
    instance({
      name: "busy",
      project: "web",
      type: "virtual-machine",
      cpu_percent: 23.33,
      memory_used: 512 * 1024 ** 2,
      memory_total: 2 * 1024 ** 3,
      rootfs_used: 7e9,
      rootfs_size: 1e10,
      disk_read_bps: 1000,
      net_rx_bps: 100,
      procs: 42,
    }),
    instance({ name: "nototal", cpu_percent: 1, memory_used: 1024 }),
  ],
};

beforeEach(() => {
  vi.spyOn(api, "fetchHosts").mockResolvedValue({ node_exporter: true, hosts: [] });
  vi.spyOn(api, "fetchTop").mockImplementation((metric) =>
    Promise.resolve({
      metric,
      unit: "percent",
      rows: metric === "cpu" ? [{ project: "web", name: "busy", value: 23.3 }] : [],
    }),
  );
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

function renderDash(props: { onExpired?: () => void } = {}) {
  return renderAt(<Overview />, { s: session(props) });
}

describe("Overview", () => {
  it("renders server, members and instances sorted by CPU", async () => {
    vi.spyOn(api, "fetchServer").mockResolvedValue(server);
    vi.spyOn(api, "fetchLiveMetrics").mockResolvedValue(live);
    renderDash();
    expect(await screen.findByText(/LXD 5.21.8 · cluster · via https:\/\/192.0.2.1:8443/)).toHaveTextContent(
      "unavailable: storage_buckets, network_peers",
    );
    const members = within(await screen.findByRole("region", { name: "Members" }));
    expect(members.getByText("ok (8443)")).toBeInTheDocument();
    expect(members.getByText("error: HTTP 403")).toBeInTheDocument();
    expect(members.getByText("ok (8443, fallback)")).toBeInTheDocument();
    expect(members.getAllByText("1d 1h")).toHaveLength(2);

    const rows = within(screen.getByRole("region", { name: "Instances" })).getAllByRole("row");
    expect(rows.map((r) => r.firstChild?.textContent)).toEqual(["Name", "web/busy", "nototal", "idle"]);
    expect(rows[1]).toHaveTextContent("VM");
    expect(rows[1]).toHaveTextContent("23.3%");
    expect(rows[1]).toHaveTextContent("512 MiB / 2.0 GiB");
    expect(rows[1]).toHaveTextContent("6.5 GiB / 9.3 GiB");
    expect(rows[1]).toHaveTextContent("1000 B/s");
    expect(rows[2]).toHaveTextContent("1.0 KiB");
    expect(rows[3]).toHaveTextContent("container");
    expect(screen.getByText(/every 15s/)).toBeInTheDocument();
    expect(within(rows[1]).getByRole("link", { name: "web/busy" })).toHaveAttribute("href", "/instances/web/busy");
    expect(members.getByRole("link", { name: "m2" })).toHaveAttribute("href", "/members/m2");

    const top = within(screen.getByRole("figure", { name: "Top CPU" }));
    expect(await top.findByRole("link", { name: "web/busy" })).toBeInTheDocument();
    expect(top.getByText("23.3%")).toBeInTheDocument();
    expect(within(screen.getByRole("figure", { name: "Top Memory" })).getByText("No data yet.")).toBeInTheDocument();
  });

  it("shows top-N errors", async () => {
    vi.spyOn(api, "fetchServer").mockResolvedValue(server);
    vi.spyOn(api, "fetchLiveMetrics").mockResolvedValue(live);
    vi.spyOn(api, "fetchTop").mockRejectedValue(new Error("no history"));
    renderDash();
    const top = within(screen.getByRole("figure", { name: "Top Disk I/O" }));
    expect(await top.findByRole("alert")).toHaveTextContent("no history");
  });

  it("filters instances", async () => {
    vi.spyOn(api, "fetchServer").mockResolvedValue({ ...server, clustered: false, features: { metrics: true } });
    vi.spyOn(api, "fetchLiveMetrics").mockResolvedValue(live);
    renderDash();
    expect(await screen.findByText("LXD 5.21.8 · standalone · via https://192.0.2.1:8443")).toBeInTheDocument();
    const filter = await screen.findByLabelText("Filter instances");
    fireEvent.change(filter, { target: { value: "WEB" } });
    const region = within(screen.getByRole("region", { name: "Instances" }));
    expect(region.getAllByRole("row")).toHaveLength(2);
    fireEvent.change(filter, { target: { value: "zzz" } });
    expect(region.getByText("No instances.")).toBeInTheDocument();
  });

  it("polls at the server interval and recovers from errors", async () => {
    vi.useFakeTimers();
    vi.spyOn(api, "fetchServer").mockResolvedValue(server);
    const poll = vi
      .spyOn(api, "fetchLiveMetrics")
      .mockResolvedValueOnce({ ...live, interval_seconds: 20 })
      .mockRejectedValueOnce(new Error("LXD: down"))
      .mockResolvedValue({ ...live, scraped_at: null });
    renderDash();
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(poll).toHaveBeenCalledTimes(1);
    await act(() => vi.advanceTimersByTimeAsync(19_999));
    expect(poll).toHaveBeenCalledTimes(1);
    await act(() => vi.advanceTimersByTimeAsync(1));
    expect(poll).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("alert")).toHaveTextContent("LXD: down");
    await act(() => vi.advanceTimersByTimeAsync(15_000));
    expect(poll).toHaveBeenCalledTimes(3);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByText(/every/)).toBeNull();
  });

  it("calls onExpired on 401 and stops polling", async () => {
    vi.useFakeTimers();
    vi.spyOn(api, "fetchServer").mockRejectedValue(new api.ApiError(401, "not logged in"));
    const poll = vi.spyOn(api, "fetchLiveMetrics").mockRejectedValue(new api.ApiError(401, "not logged in"));
    const onExpired = vi.fn();
    renderDash({ onExpired });
    await act(() => vi.advanceTimersByTimeAsync(60_000));
    expect(onExpired).toHaveBeenCalledTimes(2);
    expect(poll).toHaveBeenCalledTimes(1);
  });

  it("shows server errors and loading states", async () => {
    vi.spyOn(api, "fetchServer").mockRejectedValue("bad");
    vi.spyOn(api, "fetchLiveMetrics").mockReturnValue(new Promise(() => {}));
    renderDash();
    expect(await screen.findByRole("alert")).toHaveTextContent("bad");
    expect(screen.getByText("Connecting to LXD…")).toBeInTheDocument();
    expect(screen.getByText("Loading metrics…")).toBeInTheDocument();
  });

  it("stops after unmount", async () => {
    let resolveLive!: (m: api.LiveMetrics) => void;
    let rejectServer!: (e: unknown) => void;
    vi.spyOn(api, "fetchServer").mockReturnValue(new Promise((_, r) => (rejectServer = r)));
    const poll = vi.spyOn(api, "fetchLiveMetrics").mockReturnValue(new Promise((r) => (resolveLive = r)));
    const { unmount } = renderDash();
    unmount();
    resolveLive(live);
    rejectServer(new Error("late"));
    await Promise.resolve();
    expect(poll).toHaveBeenCalledTimes(1);
  });

  it("ignores poll failures after unmount", async () => {
    let rejectLive!: (e: unknown) => void;
    vi.spyOn(api, "fetchServer").mockResolvedValue(server);
    vi.spyOn(api, "fetchLiveMetrics").mockReturnValue(new Promise((_, r) => (rejectLive = r)));
    const onExpired = vi.fn();
    const { unmount } = renderDash({ onExpired });
    unmount();
    rejectLive(new api.ApiError(401, "x"));
    await Promise.resolve();
    expect(onExpired).not.toHaveBeenCalled();
  });
});
