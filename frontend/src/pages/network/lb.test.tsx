import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { renderAt } from "../../test/render";
import LoadBalancers from "./LoadBalancers";

const ok = { operation: null, metadata: null };
// shapes as recorded from LXD 6.10 (tests/fixtures/recorded-6.10)
const lbs: api.LoadBalancer[] = [
  {
    listen_address: "198.51.100.12",
    description: "mcui test LB",
    config: {},
    backends: [],
    ports: [{ protocol: "tcp", listen_port: "80", target_pool: "web", description: "" }],
  },
  {
    listen_address: "198.51.100.13",
    description: "",
    config: {},
    backends: [{ name: "b1", target_address: "192.0.2.4", target_port: "8080", description: "" }],
    ports: [{ protocol: "udp", listen_port: "53", target_backend: ["b1"], description: "" }],
  },
];
const pools: api.LbPool[] = [
  {
    name: "web",
    description: "",
    config: { healthcheck: "true", "healthcheck.interval": "5", protocol: "tcp", target_port: "80" },
    instances: [{ name: "c1" }, { name: "c2" }],
  },
];
const all = { pools: true, healthChecks: true };
const dialog = () => within(screen.getByRole("dialog"));

afterEach(() => vi.restoreAllMocks());

describe("LoadBalancers", () => {
  it("lists load balancers, pools and each target's health", async () => {
    vi.spyOn(api, "fetchLoadBalancers").mockResolvedValue(lbs);
    vi.spyOn(api, "fetchLbPools").mockResolvedValue(pools);
    vi.spyOn(api, "fetchLbPoolState").mockResolvedValue({
      targets: [
        { name: "c1", address: "10.0.0.2", port: "80", listen_address: "198.51.100.12", listen_port: "80", status: "online" },
        { name: "c2", address: "10.0.0.3", port: "80", listen_address: "198.51.100.12", listen_port: "80", status: "offline" },
      ],
    });
    renderAt(<LoadBalancers project="default" network="default" admin features={all} />);
    expect((await screen.findByText("198.51.100.12")).closest("tr")).toHaveTextContent("tcp 80 → pool web");
    expect(screen.getByText("198.51.100.13").closest("tr")).toHaveTextContent("udp 53 → b1b1: 192.0.2.4:8080");
    const web = (await screen.findByText("web")).closest("tr")!;
    await waitFor(() => expect(web).toHaveTextContent("c1 (online), c2 (offline)"));
    expect(web).toHaveTextContent("tcp 80");
    expect(web).toHaveTextContent("every 5 s");
  });

  it("adds a load balancer with an allocated address sending to a pool", async () => {
    vi.spyOn(api, "fetchLoadBalancers").mockResolvedValue([]);
    vi.spyOn(api, "fetchLbPools").mockResolvedValue(pools);
    vi.spyOn(api, "fetchLbPoolState").mockResolvedValue({ targets: null });
    const create = vi.spyOn(api, "createLoadBalancer").mockResolvedValue(ok);
    renderAt(<LoadBalancers project="p" network="ovn1" admin features={all} />);
    fireEvent.click(await screen.findByRole("button", { name: "Add load balancer" }));
    fireEvent.click(dialog().getByRole("button", { name: "Save" }));
    expect(dialog().getByRole("alert")).toHaveTextContent("Enter the listen address, or allocate one");
    fireEvent.click(dialog().getByLabelText(/Allocate an address/));
    fireEvent.click(dialog().getByRole("button", { name: "Add port" }));
    fireEvent.change(dialog().getByLabelText("Listen ports"), { target: { value: "443" } });
    fireEvent.click(dialog().getByRole("button", { name: "Save" }));
    expect(dialog().getByRole("alert")).toHaveTextContent("a pool or at least one backend");
    fireEvent.change(dialog().getByLabelText("Pool"), { target: { value: "web" } });
    fireEvent.click(dialog().getByRole("button", { name: "Save" }));
    expect(create).toHaveBeenCalledWith("p", "ovn1", {
      listen_address: "0.0.0.0",
      description: "",
      config: {},
      backends: [],
      ports: [{ protocol: "tcp", listen_port: "443", target_pool: "web", description: "" }],
    });
  });

  it("edits a load balancer to send to named backends; deletes", async () => {
    vi.spyOn(api, "fetchLoadBalancers").mockResolvedValue([lbs[0]]);
    vi.spyOn(api, "fetchLbPools").mockResolvedValue(pools);
    vi.spyOn(api, "fetchLbPoolState").mockResolvedValue({ targets: [] });
    const update = vi.spyOn(api, "updateLoadBalancer").mockResolvedValue(ok);
    const del = vi.spyOn(api, "deleteLoadBalancer").mockResolvedValue(ok);
    renderAt(<LoadBalancers project="default" network="default" admin features={all} />);
    const row = (await screen.findByText("198.51.100.12")).closest("tr")!;
    fireEvent.click(within(row).getByRole("button", { name: "Edit" }));
    expect(dialog().getByLabelText("Listen address")).toHaveAttribute("readonly");
    fireEvent.change(dialog().getByLabelText("Send to"), { target: { value: "backends" } });
    fireEvent.click(dialog().getByRole("button", { name: "Add backend" }));
    fireEvent.change(dialog().getByLabelText("Name"), { target: { value: "b1" } });
    fireEvent.change(dialog().getByLabelText("Target address"), { target: { value: "192.0.2.9" } });
    fireEvent.change(dialog().getByLabelText("Backends"), { target: { value: "b1, " } });
    fireEvent.click(dialog().getByRole("button", { name: "Save" }));
    expect(update).toHaveBeenCalledWith("default", "default", "198.51.100.12", {
      description: "mcui test LB",
      config: {},
      backends: [{ name: "b1", target_address: "192.0.2.9", target_port: "", description: "" }],
      ports: [{ protocol: "tcp", listen_port: "80", target_backend: ["b1"], target_pool: undefined, description: "" }],
    });
    fireEvent.click(within(row).getByRole("button", { name: "Delete" }));
    fireEvent.click(dialog().getByRole("button", { name: "Delete" }));
    expect(del).toHaveBeenCalledWith("default", "default", "198.51.100.12");
  });

  it("adds and edits pools with a health check; deletes one", async () => {
    vi.spyOn(api, "fetchLoadBalancers").mockResolvedValue([]);
    vi.spyOn(api, "fetchLbPools").mockResolvedValue(pools);
    vi.spyOn(api, "fetchLbPoolState").mockResolvedValue({ targets: [] });
    const create = vi.spyOn(api, "createLbPool").mockResolvedValue(ok);
    const update = vi.spyOn(api, "updateLbPool").mockResolvedValue(ok);
    const del = vi.spyOn(api, "deleteLbPool").mockResolvedValue(ok);
    renderAt(<LoadBalancers project="default" network="default" admin features={all} />);
    fireEvent.click(await screen.findByRole("button", { name: "Add pool" }));
    fireEvent.click(dialog().getByRole("button", { name: "Save" }));
    expect(dialog().getByRole("alert")).toHaveTextContent("Enter a name");
    fireEvent.change(dialog().getByLabelText("Name"), { target: { value: "api" } });
    fireEvent.click(dialog().getByRole("button", { name: "Save" }));
    expect(dialog().getByRole("alert")).toHaveTextContent("Enter the target port");
    fireEvent.change(dialog().getByLabelText("Target port"), { target: { value: "8080" } });
    // LXD 6.10 refuses instances from another project (checked live)
    fireEvent.change(dialog().getByLabelText("Instances in default (comma separated)"), { target: { value: "c3, c4" } });
    fireEvent.click(dialog().getByLabelText(/Health check the targets/));
    fireEvent.change(dialog().getByLabelText(/Check every/), { target: { value: "10" } });
    fireEvent.click(dialog().getByRole("button", { name: "Save" }));
    expect(create).toHaveBeenCalledWith("default", "default", "api", {
      description: "",
      protocol: "tcp",
      target_port: "8080",
      instances: ["c3", "c4"],
      healthcheck: true,
      healthcheck_interval: 10,
    });
    const web = screen.getByText("web").closest("tr")!;
    fireEvent.click(within(web).getByRole("button", { name: "Edit" }));
    fireEvent.click(dialog().getByLabelText(/Health check the targets/)); // off
    fireEvent.click(dialog().getByRole("button", { name: "Save" }));
    expect(update).toHaveBeenCalledWith("default", "default", "web", {
      description: "",
      protocol: "tcp",
      target_port: "80",
      instances: ["c1", "c2"],
      healthcheck: false,
    });
    fireEvent.click(within(web).getByRole("button", { name: "Delete" }));
    expect(dialog().getByText(/refuses while a load balancer port still sends to it/)).toBeInTheDocument();
    fireEvent.click(dialog().getByRole("button", { name: "Delete" }));
    expect(del).toHaveBeenCalledWith("default", "default", "web");
  });

  it("LXD before 6.9: no pools, ports send to backends; viewers only read", async () => {
    vi.spyOn(api, "fetchLoadBalancers").mockResolvedValue([lbs[1]]);
    const poolList = vi.spyOn(api, "fetchLbPools");
    renderAt(<LoadBalancers project="default" network="default" admin={false} features={{ pools: false, healthChecks: false }} />);
    expect(await screen.findByText("198.51.100.13")).toBeInTheDocument();
    expect(screen.queryByText("Pools")).toBeNull();
    expect(poolList).not.toHaveBeenCalled();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("explains when load balancers can't be read", async () => {
    vi.spyOn(api, "fetchLoadBalancers").mockRejectedValue(new Error("LXD down"));
    renderAt(<LoadBalancers project="default" network="default" admin features={{ pools: false, healthChecks: false }} />);
    expect(await screen.findByText("Load balancers unavailable: LXD down")).toBeInTheDocument();
  });
});
