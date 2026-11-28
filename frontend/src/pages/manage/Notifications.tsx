import { useState } from "react";
import {
  addChannel,
  deleteChannel,
  fetchChannels,
  fetchDeliveries,
  fetchNotifyKinds,
  findTelegramChats,
  testChannel,
  updateChannel,
  type NotifyChannel,
  type NotifyChannelIn,
  type NotifyKind,
  type NotifyKindSpec,
} from "../../api";
import { useActions } from "../../actions";
import Confirm from "../../Confirm";
import { useApi } from "../../useApi";

// what each setting is called in the form, with where to find it
const FIELDS: Record<string, string> = {
  url: "Webhook URL",
  signing_secret: "Signing secret (optional): signs each message, header X-MCUI-Signature",
  token: "Bot token",
  chat_id: "Chat ID",
  server: "Server",
  topic: "Topic",
  username: "Username (optional)",
  password: "Password",
};
const NTFY_TOKEN = "Access token (optional)";
const HINT: Record<NotifyKind, string> = {
  slack: "In Slack: an app with Incoming Webhooks, added to the channel. Mattermost's incoming webhooks take the same.",
  discord: "In Discord: the channel's settings, Integrations, Webhooks, New Webhook, Copy Webhook URL.",
  teams: "In Teams: the channel's Workflows, \"Post to a channel when a webhook request is received\"; its URL.",
  webhook: "A JSON POST to any URL (docs/ALERTS.md has its format).",
  telegram: "Make a bot with @BotFather, write to it (or add it to a group), then Find chat.",
  ntfy: "Anyone who knows a topic on a public server can read it: keep the random topic, or use an access token.",
};
const WHEN = (c: NotifyChannel) =>
  `${c.min_severity === "critical" ? "critical only" : "warnings and critical"}` +
  (c.reminder_hours > 0 ? `, reminders every ${c.reminder_hours} h` : "");
const time = (t: number) => new Date(t * 1000).toLocaleString();
const NAME = /^[\p{L}\p{N}_][\p{L}\p{N}_ .,()+'-]{0,63}$/u;

/** Where alerts are sent (D94): the cluster's channels (Settings), or a project's (its
 * page). A project's channels get only its alerts. */
export default function Notifications({ project }: { project?: string }) {
  const { run, version } = useActions();
  const kinds = useApi("notify:kinds", fetchNotifyKinds);
  const list = useApi(`notify:${project ?? ""}:${version}`, () => fetchChannels(project));
  const [editing, setEditing] = useState<NotifyChannel | "new" | null>(null);
  const [removing, setRemoving] = useState<NotifyChannel | null>(null);
  const [log, setLog] = useState<NotifyChannel | null>(null);
  const [tested, setTested] = useState<Record<number, string>>({});
  const test = async (c: NotifyChannel) => {
    setTested({ ...tested, [c.id]: "Sending…" });
    try {
      const r = await testChannel(project, c.id);
      setTested((t) => ({ ...t, [c.id]: r.ok ? "Sent: check it arrived" : `Failed: ${r.error}` }));
    } catch (e) {
      setTested((t) => ({ ...t, [c.id]: `Failed: ${(e as Error).message}` }));
    }
  };
  return (
    <section aria-label="Notifications">
      <h2 id="notifications">Notifications</h2>
      <p className="muted">
        {project
          ? `Alerts about project ${project} (its instances, limits and networks) sent to chat, Telegram or ntfy.`
          : "Alerts sent to chat, Telegram or ntfy. Projects can add their own channels for their alerts."}{" "}
        A channel gets an alert when it fires, when a warning turns critical and when it clears; critical alerts nobody
        has acknowledged are sent again. Secrets are never shown again once saved.
      </p>
      {list.error && <p role="alert">{list.error}</p>}
      {list.data && list.data.length === 0 && <p>No channels yet.</p>}
      {list.data && list.data.length > 0 && (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>Channel</th>
                <th>Kind</th>
                {!project && <th>Project</th>}
                <th>Sends</th>
                <th>Last message</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {list.data.map((c) => (
                <tr key={c.id}>
                  <td>
                    {c.name} {!c.enabled && <span className="muted">(off)</span>}
                  </td>
                  <td>{kinds.data?.[c.kind]?.label ?? c.kind}</td>
                  {!project && <td>{c.project ?? (c.projects.length ? `only ${c.projects.join(", ")}` : "all")}</td>}
                  <td>{WHEN(c)}</td>
                  <td>
                    {c.last ? (
                      c.last.status === "failed" ? (
                        <strong title={time(c.last.created_at)}>failed: {c.last.error}</strong>
                      ) : (
                        <span title={time(c.last.created_at)}>{c.last.status}</span>
                      )
                    ) : (
                      <span className="muted">none yet</span>
                    )}
                    {tested[c.id] && (
                      <div role="status" className="muted">
                        {tested[c.id]}
                      </div>
                    )}
                  </td>
                  <td>
                    <span className="actions">
                      <button type="button" onClick={() => void test(c)}>
                        Send test
                      </button>
                      <button type="button" onClick={() => setEditing(c)}>
                        Edit
                      </button>
                      <button type="button" onClick={() => setLog(c)}>
                        Log
                      </button>
                      <button type="button" className="danger" onClick={() => setRemoving(c)}>
                        Delete
                      </button>
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="actions">
        <button type="button" className="primary" disabled={!kinds.data} onClick={() => setEditing("new")}>
          Add channel
        </button>
      </p>
      {editing && kinds.data && (
        <ChannelDialog
          kinds={kinds.data}
          project={project}
          channel={editing === "new" ? undefined : editing}
          onClose={() => setEditing(null)}
        />
      )}
      {log && <DeliveryLog project={project} channel={log} onClose={() => setLog(null)} />}
      {removing && (
        <Confirm
          title={`Delete channel ${removing.name}?`}
          confirmLabel="Delete"
          danger
          onCancel={() => setRemoving(null)}
          onConfirm={() => {
            const c = removing;
            setRemoving(null);
            void run(`Delete channel ${c.name}`, () => deleteChannel(project, c.id));
          }}
        >
          <p>Messages waiting to be sent to it are dropped.</p>
        </Confirm>
      )}
    </section>
  );
}

const randomTopic = () => {
  const b = new Uint8Array(9);
  crypto.getRandomValues(b);
  return "mcui-" + Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
};

function ChannelDialog({
  kinds,
  project,
  channel,
  onClose,
}: {
  kinds: Record<NotifyKind, NotifyKindSpec>;
  project?: string;
  channel?: NotifyChannel;
  onClose: () => void;
}) {
  const { run } = useActions();
  const [kind, setKind] = useState<NotifyKind>(channel?.kind ?? "slack");
  const spec = kinds[kind];
  const [name, setName] = useState(channel?.name ?? "");
  const [enabled, setEnabled] = useState(channel?.enabled ?? true);
  const [minSeverity, setMinSeverity] = useState(channel?.min_severity ?? "warning");
  const [reminder, setReminder] = useState(String(channel?.reminder_hours ?? 4));
  const [config, setConfig] = useState<Record<string, string>>(
    channel?.config ?? { ...spec.defaults, ...(kind === "ntfy" ? { topic: randomTopic() } : {}) },
  );
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [projects, setProjects] = useState((channel?.projects ?? []).join(", "));
  const [exclude, setExclude] = useState((channel?.exclude_rules ?? []).join(", "));
  const [chats, setChats] = useState<{ id: string; type: string; name: string }[] | string | null>(null);
  const saved = new Set(channel?.secrets_set ?? []);
  const pickKind = (k: NotifyKind) => {
    setKind(k);
    setConfig({ ...kinds[k].defaults, ...(k === "ntfy" ? { topic: randomTopic() } : {}) });
    setSecrets({});
    setChats(null);
  };
  const label = (f: string) =>
    kind === "ntfy" && f === "token" ? NTFY_TOKEN : kind === "telegram" && f === "token" ? "Bot token (from @BotFather)" : (FIELDS[f] ?? f);
  const missing = spec.required.filter((f) => !(config[f] || secrets[f] || (saved.has(f) && secrets[f] !== "")));
  const hours = Number(reminder);
  // as the server checks: a name that shows in other channels' messages, plain words only
  const problems = [
    ...(NAME.test(name.trim()) ? [] : ["a name (letters, digits, spaces and . , ( ) + ' - _)"]),
    ...missing.map((f) => label(f)),
    ...(Number.isFinite(hours) && hours >= 0 && hours <= 168 ? [] : ["reminder hours from 0 to 168"]),
  ];
  const valid = problems.length === 0;
  const list = (v: string) =>
    v
      .split(",")
      .map((x) => x.trim())
      .filter(Boolean);
  const save = () => {
    const body: NotifyChannelIn = {
      name: name.trim(),
      kind,
      enabled,
      min_severity: minSeverity,
      config: Object.fromEntries(spec.config.map((f) => [f, config[f] ?? ""]).filter(([, v]) => v !== "")),
      // only what was typed (or cleared): the rest stays as saved
      secrets: Object.fromEntries(Object.entries(secrets).filter(([f, v]) => v !== "" || saved.has(f))),
      reminder_hours: hours,
      exclude_rules: list(exclude),
      projects: project ? [] : list(projects),
    };
    // the dialog stays open until it's saved, so a refusal ("must use https") doesn't
    // lose what was typed
    void run(channel ? `Save channel ${body.name}` : `Add channel ${body.name}`, () =>
      (channel ? updateChannel(project, channel.id, body) : addChannel(project, body)).then(() => undefined),
    ).then((ok) => ok && onClose());
  };
  const findChats = async () => {
    setChats("Looking…");
    try {
      const found = await findTelegramChats(project, secrets.token ? { token: secrets.token } : { channel_id: channel?.id });
      setChats(found.length ? found : "None seen: write to the bot (or add it to the group) and try again.");
    } catch (e) {
      setChats((e as Error).message);
    }
  };
  return (
    <Confirm title={channel ? `Edit channel ${channel.name}` : "Add a notification channel"} confirmLabel="Save" onCancel={onClose} onConfirm={() => valid && save()}>
      <div className="tall">
        <label className="field">
          Kind
          <select value={kind} disabled={!!channel} onChange={(e) => pickKind(e.target.value as NotifyKind)}>
            {(Object.keys(kinds) as NotifyKind[]).map((k) => (
              <option key={k} value={k}>
                {kinds[k].label}
              </option>
            ))}
          </select>
        </label>
        <p className="muted">{HINT[kind]}</p>
        <label className="field">
          Name
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        {spec.config.map((f) => (
          <label className="field" key={f}>
            {label(f)}
            <span className="actions">
              <input
                value={config[f] ?? ""}
                autoComplete="off"
                onChange={(e) => setConfig({ ...config, [f]: e.target.value })}
              />
              {kind === "ntfy" && f === "topic" && (
                <button type="button" onClick={() => setConfig({ ...config, topic: randomTopic() })}>
                  Random
                </button>
              )}
              {kind === "telegram" && f === "chat_id" && (
                <button type="button" disabled={!secrets.token && !saved.has("token")} onClick={() => void findChats()}>
                  Find chat
                </button>
              )}
            </span>
          </label>
        ))}
        {kind === "telegram" && chats !== null && (
          <div role="status">
            {typeof chats === "string" ? (
              <p className="muted">{chats}</p>
            ) : (
              <ul aria-label="Chats the bot has seen">
                {chats.map((c) => (
                  <li key={c.id}>
                    <button type="button" onClick={() => setConfig({ ...config, chat_id: c.id })}>
                      Use {c.name || c.id}
                    </button>{" "}
                    <span className="muted">
                      {c.type} {c.id}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
        {spec.secrets.map((f) => (
          <label className="field" key={f}>
            {label(f)}
            <span className="actions">
              <input
                type="password"
                // "off" is ignored for passwords: this stops the browser filling in the
                // user's own sign-in (and sending it to an ntfy server, D94 review)
                autoComplete="new-password"
                placeholder={saved.has(f) && secrets[f] === undefined ? "saved: leave empty to keep" : ""}
                value={secrets[f] ?? ""}
                onChange={(e) => setSecrets({ ...secrets, [f]: e.target.value })}
              />
              {saved.has(f) && !spec.required.includes(f) && (
                <button type="button" onClick={() => setSecrets({ ...secrets, [f]: "" })} disabled={secrets[f] === ""}>
                  {secrets[f] === "" ? "Will be removed" : "Remove"}
                </button>
              )}
            </span>
          </label>
        ))}
        <label className="field">
          Send
          <select value={minSeverity} onChange={(e) => setMinSeverity(e.target.value as "warning" | "critical")}>
            <option value="warning">Warnings and critical alerts</option>
            <option value="critical">Critical alerts only</option>
          </select>
        </label>
        <label className="field">
          Remind of unacknowledged critical alerts every (hours, 0 for never)
          <input type="number" className="narrow" min={0} max={168} value={reminder} onChange={(e) => setReminder(e.target.value)} />
        </label>
        {!project && (
          <label className="field">
            Only these projects&apos; alerts (comma-separated; empty for every alert)
            <input value={projects} onChange={(e) => setProjects(e.target.value)} />
          </label>
        )}
        <label className="field">
          Leave out these rules (comma-separated names, as on Settings → Alerts)
          <input value={exclude} onChange={(e) => setExclude(e.target.value)} />
        </label>
        <label>
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> On
        </label>
        {problems.length > 0 && <p className="muted">Needed: {problems.join(", ")}</p>}
      </div>
    </Confirm>
  );
}

function DeliveryLog({ project, channel, onClose }: { project?: string; channel: NotifyChannel; onClose: () => void }) {
  const log = useApi(`notify:log:${channel.id}`, () => fetchDeliveries(project, channel.id));
  return (
    <Confirm title={`Messages to ${channel.name}`} confirmLabel="Close" onCancel={onClose} onConfirm={onClose}>
      {log.error && <p role="alert">{log.error}</p>}
      {log.data && log.data.length === 0 && <p>Nothing sent yet.</p>}
      {log.data && log.data.length > 0 && (
        <ul aria-label="Recent messages" className="tall">
          {log.data.map((d) => (
            <li key={d.id}>
              {time(d.created_at)}: <strong>{d.status}</strong>
              {d.attempts > 1 && ` after ${d.attempts} tries`}
              {d.error && <span>: {d.error}</span>}
              <div className="muted">{d.events.map((e) => `${e.event} ${e.severity}: ${e.title}`).join("; ")}</div>
            </li>
          ))}
        </ul>
      )}
    </Confirm>
  );
}
