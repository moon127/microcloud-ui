import { screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { renderAt } from "../../test/render";
import OvnPage from "./OvnPage";

afterEach(() => vi.restoreAllMocks());

const net = (over: Partial<api.OvnNetwork> = {}): api.OvnNetwork => ({
  name: "default",
  project: "default",
  description: "",
  status: "Created",
  uplink: "UPLINK",
  ipv4: "10.0.0.1/24",
  ipv6: "fd42::1/64",
  ipv4_nat: true,
  ipv6_nat: false,
  router_ipv4: "192.0.2.50",
  router_ipv6: "",
  acls: ["web", "ssh"],
  used_by: 2,
  chassis: "gw-2",
  state_error: null,
  forwards: [{}],
  load_balancers: [],
  peers: null,
  allocations: { instance: 2, network: 1 },
  ...over,
});

const uplink = (over: Partial<api.OvnUplink> = {}): api.OvnUplink => ({
  name: "UPLINK",
  status: "Created",
  ipv4_gateway: "192.0.2.1/24",
  ipv6_gateway: "",
  ipv4_ranges: "192.0.2.50-192.0.2.59",
  ipv6_ranges: "",
  ipv4_routes: "10.0.0.0/16",
  dns: "192.0.2.1",
  bgp_peers: ["unifi"],
  networks: ["default"],
  ipv4_used: 3,
  ipv4_size: 10,
  ...over,
});

describe("OvnPage", () => {
  it("shows uplinks and OVN networks", async () => {
    vi.spyOn(api, "fetchOvn").mockResolvedValue({ networks: [net(), net({ name: "lab-net", project: "lab", ipv4: "", acls: [], allocations: {}, ipv6_nat: true })], uplinks: [uplink()] });
    renderAt(<OvnPage />);
    expect(await screen.findByText("All 2 OVN networks have an active gateway chassis.")).toBeInTheDocument();
    const up = within(screen.getByRole("region", { name: "Uplink UPLINK" }));
    expect(up.getByRole("link", { name: "UPLINK" })).toHaveAttribute("href", "/networks/default/UPLINK");
    expect(up.getByRole("meter", { name: "UPLINK router addresses" })).toHaveAttribute("value", "3");
    expect(up.getByText(/3 of 10 used \(192.0.2.50-192.0.2.59\)/)).toBeInTheDocument();
    expect(up.getByText("unifi")).toBeInTheDocument();
    const row = screen.getByRole("link", { name: "default" }).closest("tr")!;
    expect(row).toHaveTextContent("10.0.0.1/24 (NAT), fd42::1/64");
    expect(row).toHaveTextContent("192.0.2.50");
    expect(within(row).getByRole("link", { name: "gw-2" })).toHaveAttribute("href", "/members/gw-2");
    expect(row).toHaveTextContent("10?"); // 1 forward, 0 LBs, peers unknown
    expect(within(row).getByRole("link", { name: "ssh" })).toHaveAttribute("href", "/network-acls/default/ssh");
    expect(row).toHaveTextContent("2 instance, 1 network");
    const lab = screen.getByRole("link", { name: "lab-net" });
    expect(lab).toHaveAttribute("href", "/networks/lab/lab-net");
    expect(lab.closest("tr")).toHaveTextContent("fd42::1/64 (NAT)");
  });

  it("flags networks without a chassis, errors and full uplink ranges", async () => {
    vi.spyOn(api, "fetchOvn").mockResolvedValue({
      networks: [
        net({ chassis: null, state_error: "Network not found" }),
        net({ name: "half", status: "Errored", chassis: null, ipv4: "", ipv6: "", router_ipv4: "", acls: [], allocations: {} }),
      ],
      uplinks: [
        uplink({ ipv4_used: 9 }),
        uplink({ name: "UP2", ipv4_size: 0, ipv4_ranges: "", ipv6_ranges: "fd00::10-fd00::20", ipv4_gateway: "", bgp_peers: [], networks: [], ipv4_routes: "", dns: "" }),
      ],
    });
    renderAt(<OvnPage />);
    const problems = await screen.findByRole("list", { name: "Problems" });
    expect(problems).toHaveTextContent("default (default) has no active gateway chassis: Network not found");
    expect(problems).toHaveTextContent("half (default) is Errored");
    expect(problems).toHaveTextContent("half (default) has no active gateway chassis");
    expect(problems).toHaveTextContent("UPLINK: 9 of 10 OVN router addresses in use");
    const up2 = within(screen.getByRole("region", { name: "Uplink UP2" }));
    expect(up2.getByText("IPv6: fd00::10-fd00::20")).toBeInTheDocument();
    expect(up2.getByText("none")).toBeInTheDocument();
    const half = screen.getByRole("link", { name: "half" }).closest("tr")!;
    expect(half).toHaveTextContent("Errored");
    expect(half).toHaveTextContent("none");
  });

  it("empty and error states", async () => {
    vi.spyOn(api, "fetchOvn").mockResolvedValue({ networks: [], uplinks: [] });
    const a = renderAt(<OvnPage />);
    expect(await screen.findByText("No OVN networks or uplinks.")).toBeInTheDocument();
    a.unmount();
    vi.spyOn(api, "fetchOvn").mockResolvedValue({ networks: [], uplinks: [uplink({ networks: [] })] });
    const b = renderAt(<OvnPage />);
    expect(await screen.findByText("None.")).toBeInTheDocument();
    b.unmount();
    vi.spyOn(api, "fetchOvn").mockResolvedValue({ networks: [net()], uplinks: [] });
    const c = renderAt(<OvnPage />);
    expect(await screen.findByText("None.")).toBeInTheDocument();
    c.unmount();
    vi.spyOn(api, "fetchOvn").mockRejectedValue(new Error("down"));
    renderAt(<OvnPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("down");
  });
});
