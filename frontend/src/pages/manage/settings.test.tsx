import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { renderAt, session } from "../../test/render";
import SettingsPage from "./SettingsPage";

beforeEach(() => {
  vi.spyOn(api, "fetchAlertSettings").mockReturnValue(new Promise(() => {}));
  vi.spyOn(api, "fetchNotifyKinds").mockReturnValue(new Promise(() => {}));
  vi.spyOn(api, "fetchChannels").mockReturnValue(new Promise(() => {}));
});
afterEach(() => vi.restoreAllMocks());

const DEFAULT = ["/home", "/etc"];
const settings = (over: Partial<api.HostDeviceSettings> = {}): api.HostDeviceSettings => ({
  hidden_paths: DEFAULT,
  hidden_source: "default",
  default_hidden: DEFAULT,
  config_hidden: null,
  browse: false,
  browse_source: "default",
  config_browse: false,
  browse_idle_minutes: 10,
  browse_refresh_days: 7,
  browser: { project: "mcui-server", exists: false, ours: false, helpers: [] },
  ...over,
});
const dialog = () => within(screen.getByRole("dialog"));

describe("SettingsPage (D87)", () => {
  it("is for cluster admins only", () => {
    renderAt(<SettingsPage />, { s: session({ user: { username: "v", role: "viewer" } }) });
    expect(screen.getByRole("alert")).toHaveTextContent("Settings need the cluster admin role.");
  });

  it("edits the hidden folders", async () => {
    vi.spyOn(api, "fetchHostDeviceSettings").mockResolvedValue(settings());
    const upd = vi.spyOn(api, "updateHostDeviceSettings").mockResolvedValue(settings());
    renderAt(<SettingsPage />);
    const box = await screen.findByLabelText("Hidden folders");
    expect(box).toHaveValue("/home\n/etc");
    expect(screen.getByText(/Now: built-in default\./)).toBeInTheDocument();
    const save = screen.getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();
    fireEvent.change(box, { target: { value: "/home\nsrv\n" } });
    expect(screen.getByRole("alert")).toHaveTextContent("Not absolute: srv");
    expect(save).toBeDisabled();
    fireEvent.change(box, { target: { value: "" } });
    expect(screen.getByRole("alert")).toHaveTextContent("Nothing hidden");
    fireEvent.change(box, { target: { value: " /home \n\n/srv/private\n" } });
    fireEvent.click(save);
    expect(upd).toHaveBeenCalledWith({ hidden_paths: ["/home", "/srv/private"] });
    fireEvent.click(screen.getByRole("button", { name: "Built-in list" }));
    expect(box).toHaveValue("/home\n/etc");
    expect(screen.queryByRole("button", { name: /Use the config file/ })).toBeNull();
  });

  it("goes back to the config file's values", async () => {
    vi.spyOn(api, "fetchHostDeviceSettings").mockResolvedValue(
      settings({ hidden_source: "app", browse: true, browse_source: "app", config_hidden: ["/x"], config_browse: false }),
    );
    const upd = vi.spyOn(api, "updateHostDeviceSettings").mockResolvedValue(settings());
    renderAt(<SettingsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Use the config file" }));
    expect(upd).toHaveBeenCalledWith({ reset_hidden: true });
    fireEvent.click(screen.getByRole("button", { name: "Use the config file (off)" }));
    expect(upd).toHaveBeenLastCalledWith({ reset_browse: true });
  });

  it("switches the browser on and off, showing its helpers", async () => {
    const get = vi.spyOn(api, "fetchHostDeviceSettings").mockResolvedValue(settings());
    const upd = vi.spyOn(api, "updateHostDeviceSettings").mockResolvedValue(settings());
    const a = renderAt(<SettingsPage />);
    expect(await screen.findByText(/^Project/)).toHaveTextContent("Project mcui-server: not there.");
    fireEvent.click(screen.getByRole("button", { name: "Switch on" }));
    expect(upd).toHaveBeenCalledWith({ browse: true });
    a.unmount();
    get.mockResolvedValue(
      settings({
        hidden_source: "app",
        browse: true,
        browse_source: "config",
        config_browse: true,
        browser: {
          project: "mcui-server",
          exists: true,
          ours: true,
          image: "alpine/3.23",
          image_warning: "browse_image alpine/3.23 isn't the default (alpine/3.24), which the checks watch",
          helpers: [
            { name: "browse-m1", member: "m1", status: "Running", image: "Alpine 3.24 (default), build 20261127_0052", created_at: Date.now() / 1000 - 7200 },
            { name: "browse-m2", member: "m2", status: "Stopped", image: "", created_at: Date.now() / 1000 - 9 * 86400, stale: true },
            { name: "browse-m3", member: "m3", status: "Stopped" },
            { name: "browse-m4", member: "m4", status: "Running", image: "", created_at: Date.now() / 1000 - 9 * 86400, stale: true },
          ],
        },
      }),
    );
    const rebuild = vi.spyOn(api, "rebuildBrowserHelpers").mockResolvedValue(settings());
    const b = renderAt(<SettingsPage />);
    const table = await screen.findByRole("table", { name: "Browser helpers" });
    expect(table).toHaveTextContent("m1running" + "Alpine 3.24 (default), build 20261127_0052" + "2 h ago");
    expect(table).toHaveTextContent("9 days ago (made again when next used)");
    expect(table).toHaveTextContent("unknown");
    expect(table).toHaveTextContent("(made again after it stops)");
    expect(screen.getByText(/one older than 7 days is made again/)).toHaveTextContent("alpine/3.23");
    expect(screen.getByRole("alert")).toHaveTextContent("isn't the default (alpine/3.24)");
    fireEvent.click(screen.getByRole("button", { name: "Rebuild helpers" }));
    expect(dialog().getByText(/needs the image server to be reachable/)).toBeInTheDocument();
    fireEvent.click(dialog().getByRole("button", { name: "Cancel" }));
    expect(rebuild).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Rebuild helpers" }));
    fireEvent.click(dialog().getByRole("button", { name: "Rebuild" }));
    await waitFor(() => expect(rebuild).toHaveBeenCalled());
    expect(screen.getByRole("button", { name: /Use the config file \(it sets none/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Switch off and remove the helpers" }));
    expect(dialog().getByText(/are deleted/)).toBeInTheDocument();
    fireEvent.click(dialog().getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByRole("button", { name: "Switch off and remove the helpers" }));
    fireEvent.click(dialog().getByRole("button", { name: "Switch off" }));
    await waitFor(() => expect(upd).toHaveBeenLastCalledWith({ browse: false }));
    b.unmount();
    for (const [browser, text] of [
      [{ project: "mcui-server", exists: true, ours: false }, "isn't the app's"],
      [{ project: "mcui-server", exists: true, ours: true }, "there, no helpers yet."],
      [{ project: "mcui-server", error: "LXD away" }, "Helpers unavailable: LXD away"],
    ] as const) {
      get.mockResolvedValue(settings({ browser }));
      const c = renderAt(<SettingsPage />);
      expect(await screen.findByText(new RegExp(text.replace(/[.()]/g, "\\$&")))).toBeInTheDocument();
      c.unmount();
    }
    get.mockResolvedValue(settings({ browser: null }));
    const d = renderAt(<SettingsPage />);
    await screen.findByLabelText("Hidden folders");
    expect(screen.queryByText(/Project mcui/)).toBeNull();
    d.unmount();
    get.mockRejectedValue(new Error("boom"));
    renderAt(<SettingsPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
  });

  it("switching off with no project yet names the default", async () => {
    vi.spyOn(api, "fetchHostDeviceSettings").mockResolvedValue(settings({ browse: true, browser: null }));
    renderAt(<SettingsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Switch off and remove the helpers" }));
    expect(dialog().getByText(/mcui-server/)).toBeInTheDocument();
  });
});
