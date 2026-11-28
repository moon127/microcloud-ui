import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { renderAt } from "../../test/render";
import Notifications from "./Notifications";

const KINDS: Record<api.NotifyKind, api.NotifyKindSpec> = {
  slack: { label: "Slack (or Mattermost)", config: [], secrets: ["url"], required: ["url"], defaults: {} },
  discord: { label: "Discord", config: [], secrets: ["url"], required: ["url"], defaults: {} },
  teams: { label: "Microsoft Teams (Workflows)", config: [], secrets: ["url"], required: ["url"], defaults: {} },
  webhook: { label: "Webhook (JSON)", config: [], secrets: ["url", "signing_secret"], required: ["url"], defaults: {} },
  telegram: { label: "Telegram", config: ["chat_id"], secrets: ["token"], required: ["chat_id", "token"], defaults: {} },
  ntfy: {
    label: "ntfy",
    config: ["server", "topic", "username"],
    secrets: ["token", "password"],
    required: ["server", "topic"],
    defaults: { server: "https://ntfy.sh" },
  },
};
const chan = (over: Partial<api.NotifyChannel> = {}): api.NotifyChannel => ({
  id: 1,
  name: "ops",
  kind: "slack",
  project: null,
  enabled: true,
  min_severity: "warning",
  config: {},
  secrets_set: ["url"],
  reminder_hours: 4,
  exclude_rules: [],
  projects: [],
  created_by: "admin",
  created_at: 1_800_000_000,
  last: null,
  ...over,
});
const dialog = () => within(screen.getByRole("dialog"));

beforeEach(() => {
  vi.spyOn(api, "fetchNotifyKinds").mockResolvedValue(KINDS);
});
afterEach(() => vi.restoreAllMocks());

describe("Notifications (D94)", () => {
  it("lists the cluster's channels, sends a test and shows the log", async () => {
    vi.spyOn(api, "fetchChannels").mockResolvedValue([
      chan(),
      chan({
        id: 2,
        name: "web-team",
        kind: "webhook",
        project: "web",
        min_severity: "critical",
        reminder_hours: 0,
        enabled: false,
        last: { status: "failed", error: "HTTP 404", created_at: 1_800_000_000, sent_at: null },
      }),
      chan({ id: 3, name: "lab", projects: ["lab"], last: { status: "sent", error: null, created_at: 1, sent_at: 1 } }),
    ]);
    const test = vi.spyOn(api, "testChannel").mockResolvedValueOnce({ ok: true }).mockResolvedValueOnce({ ok: false, error: "HTTP 500" });
    vi.spyOn(api, "fetchDeliveries").mockResolvedValue([
      { id: 9, status: "failed", attempts: 7, created_at: 1, sent_at: null, error: "timed out", events: [{ event: "fired", severity: "critical", title: "osd.2 full" }] },
    ]);
    renderAt(<Notifications />);
    const row = (name: string) => screen.getByText(name).closest("tr")!;
    expect(await screen.findByRole("heading", { name: "Notifications" })).toHaveAttribute("id", "notifications");
    expect(row("ops")).toHaveTextContent("Slack (or Mattermost)allwarnings and critical, reminders every 4 hnone yet");
    expect(row("web-team")).toHaveTextContent("(off)");
    expect(row("web-team")).toHaveTextContent("webcritical onlyfailed: HTTP 404");
    expect(row("lab")).toHaveTextContent("only lab");
    fireEvent.click(within(row("ops")).getByRole("button", { name: "Send test" }));
    expect(await within(row("ops")).findByText("Sent: check it arrived")).toBeInTheDocument();
    fireEvent.click(within(row("ops")).getByRole("button", { name: "Send test" }));
    expect(await within(row("ops")).findByText("Failed: HTTP 500")).toBeInTheDocument();
    expect(test).toHaveBeenCalledWith(undefined, 1);
    fireEvent.click(within(row("web-team")).getByRole("button", { name: "Log" }));
    expect(await dialog().findByText(/after 7 tries/)).toHaveTextContent("failed after 7 tries: timed out");
    expect(dialog().getByText("fired critical: osd.2 full")).toBeInTheDocument();
    fireEvent.click(dialog().getByRole("button", { name: "Close" }));
    const del = vi.spyOn(api, "deleteChannel").mockResolvedValue(undefined);
    fireEvent.click(within(row("lab")).getByRole("button", { name: "Delete" }));
    fireEvent.click(dialog().getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(del).toHaveBeenCalledWith(undefined, 3));
  });

  it("adds a Slack channel, and keeps or replaces a saved secret when editing", async () => {
    vi.spyOn(api, "fetchChannels").mockResolvedValue([chan()]);
    const add = vi.spyOn(api, "addChannel").mockResolvedValue(chan());
    const upd = vi.spyOn(api, "updateChannel").mockResolvedValue(chan());
    renderAt(<Notifications />);
    fireEvent.click(await screen.findByRole("button", { name: "Add channel" }));
    expect(dialog().getByText(/Incoming Webhooks/)).toBeInTheDocument();
    expect(dialog().getByText(/^Needed: a name .*, Webhook URL$/)).toBeInTheDocument();
    fireEvent.change(dialog().getByLabelText("Name"), { target: { value: "<!here>" } });
    expect(dialog().getByText(/^Needed: a name/)).toBeInTheDocument(); // it shows in other channels
    fireEvent.change(dialog().getByLabelText("Name"), { target: { value: " ops " } });
    fireEvent.change(dialog().getByLabelText("Webhook URL"), { target: { value: "https://hooks.slack.com/x" } });
    fireEvent.change(dialog().getByLabelText(/Only these projects/), { target: { value: "web, lab," } });
    fireEvent.change(dialog().getByLabelText(/Leave out these rules/), { target: { value: "ceph_raw" } });
    fireEvent.change(dialog().getByLabelText("Send"), { target: { value: "critical" } });
    expect(dialog().queryByText(/^Needed/)).toBeNull();
    expect(dialog().getByLabelText("Webhook URL")).toHaveAttribute("autocomplete", "new-password");
    // refused: the dialog stays, with what was typed
    add.mockRejectedValueOnce(new api.ApiError(400, "url must use https"));
    fireEvent.click(dialog().getByRole("button", { name: "Save" }));
    await waitFor(() => expect(add).toHaveBeenCalledTimes(1));
    expect(dialog().getByLabelText("Webhook URL")).toHaveValue("https://hooks.slack.com/x");
    fireEvent.click(dialog().getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() =>
      expect(add).toHaveBeenLastCalledWith(undefined, {
        name: "ops",
        kind: "slack",
        enabled: true,
        min_severity: "critical",
        config: {},
        secrets: { url: "https://hooks.slack.com/x" },
        reminder_hours: 4,
        exclude_rules: ["ceph_raw"],
        projects: ["web", "lab"],
      }),
    );
    // editing: the URL is saved, so it may stay empty; the kind can't change
    fireEvent.click(within(screen.getByText("ops").closest("tr")!).getByRole("button", { name: "Edit" }));
    expect(dialog().getByLabelText("Kind")).toBeDisabled();
    expect(dialog().getByLabelText("Webhook URL")).toHaveAttribute("placeholder", "saved: leave empty to keep");
    fireEvent.change(dialog().getByLabelText(/Remind/), { target: { value: "0" } });
    fireEvent.click(dialog().getByRole("button", { name: "Save" }));
    await waitFor(() => expect(upd).toHaveBeenCalledWith(undefined, 1, expect.objectContaining({ secrets: {}, reminder_hours: 0 })));
  });

  it("sets up ntfy with a random topic and removes a saved optional secret", async () => {
    vi.spyOn(api, "fetchChannels").mockResolvedValue([
      chan({ kind: "ntfy", config: { server: "https://ntfy.sh", topic: "ops" }, secrets_set: ["token"] }),
    ]);
    const add = vi.spyOn(api, "addChannel").mockResolvedValue(chan());
    const upd = vi.spyOn(api, "updateChannel").mockResolvedValue(chan());
    renderAt(<Notifications />);
    fireEvent.click(await screen.findByRole("button", { name: "Add channel" }));
    fireEvent.change(dialog().getByLabelText("Kind"), { target: { value: "ntfy" } });
    expect(dialog().getByLabelText("Server")).toHaveValue("https://ntfy.sh");
    const topic = dialog().getByLabelText("Topic") as HTMLInputElement;
    expect(topic.value).toMatch(/^mcui-[0-9a-f]{18}$/);
    const first = topic.value;
    fireEvent.click(dialog().getByRole("button", { name: "Random" }));
    expect(topic.value).not.toBe(first);
    fireEvent.change(dialog().getByLabelText("Name"), { target: { value: "phone" } });
    fireEvent.change(dialog().getByLabelText(/Remind/), { target: { value: "500" } });
    fireEvent.click(dialog().getByRole("button", { name: "Save" }));
    expect(add).not.toHaveBeenCalled(); // over a week: refused before sending
    fireEvent.change(dialog().getByLabelText(/Remind/), { target: { value: "4" } });
    fireEvent.click(dialog().getByRole("button", { name: "Save" }));
    await waitFor(() => expect(add).toHaveBeenCalledWith(undefined, expect.objectContaining({ kind: "ntfy", config: { server: "https://ntfy.sh", topic: topic.value } })));
    fireEvent.click(within(screen.getByText("ops").closest("tr")!).getByRole("button", { name: "Edit" }));
    fireEvent.click(dialog().getByRole("button", { name: "Remove" }));
    expect(dialog().getByRole("button", { name: "Will be removed" })).toBeDisabled();
    fireEvent.click(dialog().getByRole("button", { name: "Save" }));
    await waitFor(() => expect(upd).toHaveBeenCalledWith(undefined, 1, expect.objectContaining({ secrets: { token: "" } })));
  });

  it("finds a Telegram chat for a project's channel", async () => {
    vi.spyOn(api, "fetchChannels").mockResolvedValue([]);
    const find = vi
      .spyOn(api, "findTelegramChats")
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([{ id: "-100", type: "group", name: "Ops" }])
      .mockRejectedValueOnce(new Error("Telegram: HTTP 401"));
    const add = vi.spyOn(api, "addChannel").mockResolvedValue(chan());
    renderAt(<Notifications project="web" />);
    expect(await screen.findByText("No channels yet.")).toBeInTheDocument();
    expect(screen.getByText(/Alerts about project web/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add channel" }));
    fireEvent.change(dialog().getByLabelText("Kind"), { target: { value: "telegram" } });
    expect(dialog().queryByLabelText(/Only these projects/)).toBeNull(); // a project's: its own alerts
    const findBtn = dialog().getByRole("button", { name: "Find chat" });
    expect(findBtn).toBeDisabled();
    fireEvent.change(dialog().getByLabelText("Bot token (from @BotFather)"), { target: { value: "1:abc" } });
    fireEvent.click(findBtn);
    expect(await dialog().findByText(/None seen/)).toBeInTheDocument();
    fireEvent.click(findBtn);
    fireEvent.click(await dialog().findByRole("button", { name: "Use Ops" }));
    expect(dialog().getByLabelText("Chat ID")).toHaveValue("-100");
    expect(find).toHaveBeenCalledWith("web", { token: "1:abc" });
    fireEvent.click(findBtn);
    expect(await dialog().findByText("Telegram: HTTP 401")).toBeInTheDocument();
    fireEvent.change(dialog().getByLabelText("Name"), { target: { value: "tg" } });
    fireEvent.click(dialog().getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(add).toHaveBeenCalledWith("web", expect.objectContaining({ kind: "telegram", config: { chat_id: "-100" }, secrets: { token: "1:abc" }, projects: [] })),
    );
  });

  it("finds chats with a saved bot's token, and reports a failed test", async () => {
    vi.spyOn(api, "fetchChannels").mockResolvedValue([chan({ kind: "telegram", config: { chat_id: "5" }, secrets_set: ["token"] })]);
    const find = vi.spyOn(api, "findTelegramChats").mockResolvedValue([{ id: "5", type: "private", name: "" }]);
    vi.spyOn(api, "testChannel").mockRejectedValue(new Error("needs the manage right"));
    renderAt(<Notifications />);
    const row = (await screen.findByText("ops")).closest("tr")!;
    fireEvent.click(within(row).getByRole("button", { name: "Send test" }));
    expect(await within(row).findByText("Failed: needs the manage right")).toBeInTheDocument();
    fireEvent.click(within(row).getByRole("button", { name: "Edit" }));
    fireEvent.click(dialog().getByRole("button", { name: "Find chat" }));
    expect(await dialog().findByRole("button", { name: "Use 5" })).toBeInTheDocument();
    expect(find).toHaveBeenCalledWith(undefined, { channel_id: 1 });
  });
});
