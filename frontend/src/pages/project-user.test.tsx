// What a project user sees (D81, Phase 6c): the server enforces it; the UI stops offering
// what it would refuse.
import { screen, within } from "@testing-library/react";
import { Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import Layout from "../Layout";
import { renderAt, session } from "../test/render";
import InstancePage from "./instance/InstancePage";
import Overview from "./Overview";

afterEach(() => vi.restoreAllMocks());

const as = (user: api.User) => session({ user });
const pat = as({ username: "pat", role: "project", grants: { web: "operator", lab: "viewer", ops: "admin" } });
const viewer = as({ username: "v", role: "viewer" });

function nav(s = session()) {
  renderAt(
    <Routes>
      <Route element={<Layout />}>
        <Route path="*" element={<p>child</p>} />
      </Route>
    </Routes>,
    { s },
  );
  return within(screen.getByRole("navigation", { name: "Main" }))
    .getAllByRole("link")
    .map((l) => l.textContent);
}

describe("navigation by role", () => {
  beforeEach(() => vi.spyOn(api, "fetchProjects").mockResolvedValue([]));

  it("cluster admins see everything, including Users", () => {
    expect(nav()).toContain("Users");
  });

  it("viewers see the cluster pages but not Users", () => {
    const links = nav(viewer);
    expect(links).toEqual(expect.arrayContaining(["Storage", "Ceph", "Warnings"]));
    expect(links).not.toContain("Users");
  });

  it("project users see only project pages", () => {
    expect(nav(pat)).toEqual(["Overview", "Instances", "Profiles", "Images", "Projects", "Networks", "Operations", "Audit"]);
    expect(screen.getByText("pat (project)")).toBeInTheDocument();
  });
});

const inst = (project: string): api.InstanceFull => ({
  project,
  name: "c1",
  type: "container",
  status: "Running",
  location: "m1",
  description: "",
  profiles: ["default"],
  config: {},
  devices: {},
  expanded_devices: {},
  snapshots: [],
});

function open(project: string, s = session()) {
  vi.spyOn(api, "queryMetric").mockReturnValue(new Promise(() => {}));
  vi.spyOn(api, "fetchInstance").mockResolvedValue(inst(project));
  renderAt(<InstancePage />, { url: `/instances/${project}/c1`, path: "/instances/:project/:name", s });
}
const tabs = () =>
  within(screen.getByRole("navigation", { name: "Instance sections" }))
    .getAllByRole("button")
    .map((b) => b.textContent);

describe("an instance by the user's right in its project", () => {
  it("viewers get no files, consoles or actions", async () => {
    open("default", viewer);
    expect(await screen.findByText(/container · Running/)).toBeInTheDocument();
    expect(tabs()).toEqual(["Overview", "Configuration", "Snapshots", "Logs"]);
    expect(screen.queryByRole("toolbar", { name: "Instance actions" })).toBeNull();
  });

  it("operators run it but don't rename, move or delete it", async () => {
    open("web", pat);
    const bar = within(await screen.findByRole("toolbar", { name: "Instance actions" }));
    expect(tabs()).toContain("Files");
    expect(tabs()).toContain("Console");
    expect(bar.getByRole("button", { name: "Stop" })).toBeInTheDocument();
    expect(bar.queryByRole("button", { name: "Delete" })).toBeNull();
    expect(bar.queryByRole("button", { name: "Rename" })).toBeNull();
    // member pages are cluster-wide: the member is named, not linked
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("on m1");
    expect(screen.queryByRole("link", { name: "m1" })).toBeNull();
  });

  it("project admins get everything; a viewer grant gets nothing", async () => {
    open("ops", pat);
    const bar = within(await screen.findByRole("toolbar", { name: "Instance actions" }));
    expect(bar.getByRole("button", { name: "Delete" })).toBeInTheDocument();
  });

  it("a viewer grant shows no actions", async () => {
    open("lab", pat);
    expect(await screen.findByText(/container · Running/)).toBeInTheDocument();
    expect(screen.queryByRole("toolbar", { name: "Instance actions" })).toBeNull();
    expect(tabs()).not.toContain("Files");
  });
});

describe("the Overview for a project user", () => {
  it("has no hosts or members, which are cluster-wide", async () => {
    vi.spyOn(api, "fetchServer").mockResolvedValue({
      version: "6.10.1", clustered: true, server_name: "m1", auth: "trusted", auth_method: "tls", endpoint: "https://192.0.2.1:8443", features: {},
    });
    vi.spyOn(api, "fetchLiveMetrics").mockResolvedValue({ scraped_at: 1, interval_seconds: 15, members: [], instances: [] });
    const hosts = vi.spyOn(api, "fetchHosts");
    vi.spyOn(api, "fetchTop").mockReturnValue(new Promise(() => {}));
    renderAt(<Overview />, { s: pat });
    expect(await screen.findByText(/LXD 6.10.1/)).toBeInTheDocument();
    expect(screen.getByText(/LXD 6.10.1/)).not.toHaveTextContent("via");
    expect(screen.queryByRole("heading", { name: "Members" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Hosts" })).toBeNull();
    expect(hosts).not.toHaveBeenCalled();
  });
});
