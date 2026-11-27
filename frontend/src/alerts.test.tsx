import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "./api";
import Layout from "./Layout";
import AlertsPage from "./pages/AlertsPage";
import SettingsPage from "./pages/manage/SettingsPage";
import { renderAt, session } from "./test/render";

const T = 1_800_000_000;
const alert = (over: Partial<api.AlertRow> = {}): api.AlertRow => ({
  id: 1,
  key: "ceph_health:MON_DISK_LOW",
  rule: "ceph_health",
  severity: "warning",
  title: "A Ceph monitor's disk is low on space",
  detail: "Ceph health check MON_DISK_LOW (HEALTH_WARN)",
  subject: "MON_DISK_LOW",
  link: "/ceph",
  project: null,
  fired_at: T,
  last_seen: T + 60,
  resolved_at: null,
  ack_by: null,
  ack_at: null,
  data: {},
  ...over,
});
const OOM = alert({ id: 2, key: "instance_oom:web/c1", rule: "instance_oom", title: "c1 ran out of memory", project: "web", link: "/instances/web/c1" });

beforeEach(() => {
  sessionStorage.clear();
  vi.spyOn(api, "fetchProjects").mockResolvedValue([]);
  vi.spyOn(api, "fetchWarnings").mockResolvedValue([]);
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("Alerts page (D91)", () => {
  it("lists active alerts, acknowledges, shows history and LXD's warnings", async () => {
    const list = vi.spyOn(api, "fetchAlerts").mockImplementation((state) =>
      Promise.resolve(
        state === "active"
          ? [alert(), OOM, alert({ id: 3, title: "Acked", ack_by: "bob", link: "" })]
          : [alert({ id: 9, resolved_at: T + 3600, title: "Old one" })],
      ),
    );
    const ack = vi.spyOn(api, "ackAlert").mockResolvedValue(alert({ ack_by: "alice" }));
    renderAt(<AlertsPage />, { url: "/alerts" });
    const row = (await screen.findByRole("link", { name: "A Ceph monitor's disk is low on space" })).closest("tr")!;
    expect(row).toHaveTextContent("warning");
    expect(screen.getByText("Acked").closest("tr")).toHaveTextContent("by bob");
    fireEvent.click(within(row).getByRole("button", { name: "Acknowledge" }));
    expect(ack).toHaveBeenCalledWith(1);
    fireEvent.click(screen.getByRole("button", { name: "History" }));
    expect(await screen.findByText("Old one")).toBeInTheDocument();
    expect(screen.getByText("Old one").closest("tr")).toHaveTextContent("(after 1h");
    expect(list).toHaveBeenCalledWith("history");
    fireEvent.click(screen.getByRole("button", { name: "LXD warnings" }));
    expect(await screen.findByText("No warnings.")).toBeInTheDocument();
  });

  it("a project user sees no LXD warnings tab and acknowledges only where they manage", async () => {
    vi.spyOn(api, "fetchAlerts").mockResolvedValue([OOM, alert({ id: 4, project: "lab", title: "lab one", link: "" })]);
    renderAt(<AlertsPage />, {
      url: "/alerts?tab=lxd",
      s: session({ user: { username: "p", role: "project", grants: { web: "admin", lab: "viewer" } } }),
    });
    expect(await screen.findByText("c1 ran out of memory")).toBeInTheDocument(); // lxd tab unknown: active
    expect(screen.queryByRole("button", { name: "LXD warnings" })).toBeNull();
    expect(within(screen.getByText("c1 ran out of memory").closest("tr")!).getByRole("button", { name: "Acknowledge" })).toBeInTheDocument();
    expect(screen.getByText("lab one").closest("tr")).toHaveTextContent("–");
  });

  it("empty, loading and errors", async () => {
    const list = vi.spyOn(api, "fetchAlerts").mockResolvedValue([]);
    const a = renderAt(<AlertsPage />, { url: "/alerts" });
    expect(await screen.findByText("Nothing wrong right now.")).toBeInTheDocument();
    a.unmount();
    const b = renderAt(<AlertsPage />, { url: "/alerts?tab=history" });
    expect(await screen.findByText("No cleared alerts yet.")).toBeInTheDocument();
    b.unmount();
    list.mockRejectedValue(new Error("boom"));
    renderAt(<AlertsPage />, { url: "/alerts" });
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
  });
});

function layout() {
  return renderAt(
    <Routes>
      <Route element={<Layout />}>
        <Route path="*" element={<p>child</p>} />
      </Route>
    </Routes>,
    { url: "/instances" },
  );
}

describe("the bell, toasts and banner (D91)", () => {
  it("counts alerts, pops a toast for new ones only, and bannerises critical ones", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const list = vi.spyOn(api, "fetchAlerts").mockResolvedValue([alert()]);
    layout();
    const bell = await screen.findByRole("link", { name: "Alerts: 0 critical, 1 warning" });
    expect(bell).toHaveTextContent("1");
    const toastList = () => screen.getByRole("list", { name: "New alerts" });
    expect(within(toastList()).queryAllByRole("listitem")).toHaveLength(0); // already there: not news
    const crit = alert({ id: 5, severity: "critical", title: "OSD full", link: "" });
    list.mockResolvedValue([alert(), OOM, crit]);
    await act(() => vi.advanceTimersByTimeAsync(15_000));
    const toasts = toastList();
    await waitFor(() => expect(toasts).toHaveTextContent("Warning: c1 ran out of memory"));
    expect(toasts).toHaveTextContent("Critical: OSD full");
    expect(screen.getByRole("link", { name: "Alerts: 1 critical, 2 warning" })).toBeInTheDocument();
    expect(screen.getByText(/OSD full/, { selector: ".alert-banner" })).toBeInTheDocument();
    // warnings go after 20 s; critical ones stay until dismissed
    await act(() => vi.advanceTimersByTimeAsync(21_000));
    expect(screen.getByRole("list", { name: "New alerts" })).not.toHaveTextContent("c1 ran out of memory");
    fireEvent.click(within(toastList()).getByRole("button", { name: "Dismiss OSD full" }));
    expect(within(toastList()).queryAllByRole("listitem")).toHaveLength(0);
    // two critical ones: counted in the banner
    list.mockResolvedValue([crit, alert({ id: 6, severity: "critical", title: "MON down" })]);
    await act(() => vi.advanceTimersByTimeAsync(15_000));
    await waitFor(() => expect(screen.getByText(/2 critical alerts: OSD full/)).toBeInTheDocument());
  });

  it("remembers what it showed across a reload, and copes without storage", async () => {
    sessionStorage.setItem("mcui.alerts.seen", JSON.stringify(["1:warning"]));
    vi.spyOn(api, "fetchAlerts").mockResolvedValue([alert(), OOM]);
    const a = layout();
    await waitFor(() => expect(screen.getByRole("list", { name: "New alerts" })).toHaveTextContent("c1 ran out of memory"));
    a.unmount();
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    layout();
    expect(await screen.findByRole("link", { name: /Alerts: 0 critical, 2 warning/ })).toBeInTheDocument();
    expect(within(screen.getByRole("list", { name: "New alerts" })).queryAllByRole("listitem")).toHaveLength(0);
  });

  it("announces a warning turning critical, and withdraws toasts that cleared or were acknowledged", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const list = vi.spyOn(api, "fetchAlerts").mockResolvedValue([alert({ ack_by: "bob" })]);
    const { unmount } = layout();
    await screen.findByRole("link", { name: "Alerts: 0 critical, 0 warning" });
    const toastList = () => screen.getByRole("list", { name: "New alerts" });
    // acknowledged as a warning; now critical, and the server undid the acknowledgement
    list.mockResolvedValue([alert({ severity: "critical", title: "Disk full" }), OOM]);
    await act(() => vi.advanceTimersByTimeAsync(15_000));
    await waitFor(() => expect(toastList()).toHaveTextContent("Critical: Disk full"));
    expect(screen.getByText("Disk full", { selector: ".alert-banner" })).toBeInTheDocument();
    // acknowledged and cleared: their toasts go, though nobody dismissed them
    list.mockResolvedValue([alert({ severity: "critical", title: "Disk full", ack_by: "alice" })]);
    await act(() => vi.advanceTimersByTimeAsync(15_000));
    await waitFor(() => expect(within(toastList()).queryAllByRole("listitem")).toHaveLength(0));
    unmount(); // its warning timer is cleared with it
    await act(() => vi.advanceTimersByTimeAsync(21_000));
  });
});

const SETTINGS: api.AlertSettingsView = {
  rules: [
    { name: "ceph_health", description: "Ceph's own health checks", threshold: null, for_seconds: 0 },
    { name: "local_pool", description: "Local storage pools (%)", threshold: "local_pool", for_seconds: 60 },
    { name: "cert_expiry", description: "Certificates (days left)", threshold: "cert_days", for_seconds: 0 },
  ],
  thresholds: { local_pool: [80, 90], cert_days: [30, 7] },
  default_thresholds: { local_pool: [80, 90], cert_days: [30, 7] },
  thresholds_saved: false,
  disabled: [],
  disabled_saved: false,
  enabled: true,
  interval_seconds: 30,
  retention_days: 90,
};

describe("alert settings (D91)", () => {
  beforeEach(() => {
    vi.spyOn(api, "fetchHostDeviceSettings").mockReturnValue(new Promise(() => {}));
  });

  it("switches rules off and changes thresholds, with checks", async () => {
    vi.spyOn(api, "fetchAlertSettings").mockResolvedValue(SETTINGS);
    const upd = vi.spyOn(api, "updateAlertSettings").mockResolvedValue(SETTINGS);
    renderAt(<SettingsPage />);
    const section = within(await screen.findByRole("region", { name: "Alerts" }));
    expect(section.getByText("Ceph's own health checks").closest("tr")).toHaveTextContent("as reported");
    const save = section.getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();
    fireEvent.change(section.getByLabelText("local_pool warning"), { target: { value: "95" } });
    fireEvent.click(save);
    expect(section.getByRole("alert")).toHaveTextContent("local_pool: critical must be past warning");
    fireEvent.change(section.getByLabelText("local_pool warning"), { target: { value: "" } });
    fireEvent.change(section.getByLabelText("local_pool warning"), { target: { value: "abc" } });
    fireEvent.change(section.getByLabelText("cert_days critical"), { target: { value: "40" } });
    fireEvent.click(save);
    expect(section.getByRole("alert")).toHaveTextContent(/critical must be past warning|numbers only/);
    // emptied isn't 0, and the server's ranges hold
    fireEvent.change(section.getByLabelText("local_pool warning"), { target: { value: "" } });
    fireEvent.change(section.getByLabelText("cert_days critical"), { target: { value: "5" } });
    fireEvent.click(save);
    expect(section.getByRole("alert")).toHaveTextContent("local_pool: numbers only");
    fireEvent.change(section.getByLabelText("local_pool warning"), { target: { value: "0" } });
    fireEvent.click(save);
    expect(section.getByRole("alert")).toHaveTextContent("local_pool: percent between 0 and 100");
    fireEvent.change(section.getByLabelText("local_pool warning"), { target: { value: "70" } });
    fireEvent.change(section.getByLabelText("cert_days critical"), { target: { value: "0" } });
    fireEvent.click(save);
    expect(section.getByRole("alert")).toHaveTextContent("cert_days: days above 0");
    fireEvent.change(section.getByLabelText("cert_days critical"), { target: { value: "5" } });
    fireEvent.click(section.getByLabelText("ceph_health on"));
    fireEvent.click(save);
    expect(upd).toHaveBeenCalledWith({ thresholds: { local_pool: [70, 90], cert_days: [30, 5] }, disabled: ["ceph_health"] });
    fireEvent.click(section.getByLabelText("ceph_health on")); // back on
    fireEvent.click(section.getByRole("button", { name: "Built-in thresholds" }));
    expect(section.getByLabelText("local_pool warning")).toHaveValue(80);
    expect(section.queryByRole("button", { name: "Use the config file" })).toBeNull();
  });

  it("goes back to the config file, and says when alerts are off", async () => {
    vi.spyOn(api, "fetchAlertSettings").mockResolvedValue({ ...SETTINGS, disabled_saved: true, enabled: false, disabled: ["ceph_health"] });
    const upd = vi.spyOn(api, "updateAlertSettings").mockResolvedValue(SETTINGS);
    renderAt(<SettingsPage />);
    const section = within(await screen.findByRole("region", { name: "Alerts" }));
    expect(section.getByText(/switched off in the config file/)).toBeInTheDocument();
    expect(section.getByLabelText("ceph_health on")).not.toBeChecked();
    fireEvent.click(section.getByRole("button", { name: "Use the config file" }));
    expect(upd).toHaveBeenCalledWith({ reset_thresholds: true, reset_disabled: true });
  });

  it("an error loading them", async () => {
    vi.spyOn(api, "fetchAlertSettings").mockRejectedValue(new Error("nope"));
    renderAt(<SettingsPage />);
    expect(await screen.findByText("nope")).toBeInTheDocument();
  });
});
