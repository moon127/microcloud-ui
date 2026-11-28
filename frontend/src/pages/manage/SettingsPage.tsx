import { useState } from "react";
import {
  fetchAlertSettings,
  fetchHostDeviceSettings,
  updateAlertSettings,
  rebuildBrowserHelpers,
  updateHostDeviceSettings,
  type AlertSettingsChange,
  type AlertSettingsView,
  type HostDeviceSettings,
  type SettingSource,
} from "../../api";
import { useAccess } from "../../access";
import { useActions } from "../../actions";
import Confirm from "../../Confirm";
import { useApi } from "../../useApi";
import { useHashScroll } from "../../hashScroll";
import Notifications from "./Notifications";

/** How long ago a timestamp (seconds) was, roughly. */
const age = (t: number) => {
  const h = Math.max(0, (Date.now() / 1000 - t) / 3600);
  return h < 48 ? `${Math.max(1, Math.round(h))} h` : `${Math.round(h / 24)} days`;
};

const SOURCE: Record<SettingSource, string> = {
  app: "saved here",
  config: "from the config file",
  default: "built-in default",
};

/** Settings a cluster admin changes here instead of in the config file (D87): the folders
 * hidden from project admins, and the folder browser. Saved values win over the file. */
export default function SettingsPage() {
  const access = useAccess();
  const { version } = useActions();
  const s = useApi(`settings:host-devices:${version}`, fetchHostDeviceSettings);
  const al = useApi(`settings:alerts:${version}`, fetchAlertSettings);
  useHashScroll(al.data);
  if (!access.clusterAdmin) return <p role="alert">Settings need the cluster admin role.</p>;
  return (
    <>
      <h1>Settings</h1>
      <p className="muted">
        Saved here, a setting wins over the config file&apos;s <code>[alerts]</code> and <code>[host_devices]</code> sections;{" "}
        <strong>Use the config file</strong> goes back to it.
      </p>
      {s.error && <p role="alert">{s.error}</p>}
      {!s.data && !s.error && <p role="status">Loading…</p>}
      {al.error && <p role="alert">{al.error}</p>}
      {al.data && <Alerts key={JSON.stringify(al.data)} s={al.data} />}
      <Notifications />
      {s.data && <HostDevices key={JSON.stringify(s.data)} s={s.data} />}
    </>
  );
}

/** Alert rules: on or off, and their thresholds (D91). */
function Alerts({ s }: { s: AlertSettingsView }) {
  const { run } = useActions();
  const [off, setOff] = useState(new Set(s.disabled));
  const [limits, setLimits] = useState(s.thresholds);
  const [problem, setProblem] = useState("");
  const changedOff = [...off].sort().join() !== [...s.disabled].sort().join();
  const changedLimits = JSON.stringify(limits) !== JSON.stringify(s.thresholds);
  const set = (name: string, i: 0 | 1, v: string) => {
    const next = [...limits[name]] as [number, number];
    next[i] = v.trim() === "" ? NaN : Number(v); // empty isn't 0: that would alert on everything
    setLimits({ ...limits, [name]: next });
  };
  const save = () => {
    for (const [name, [warn, crit]] of Object.entries(limits)) {
      if (!Number.isFinite(warn) || !Number.isFinite(crit)) return setProblem(`${name}: numbers only`);
      if (name === "cert_days" ? crit > warn : crit < warn) return setProblem(`${name}: critical must be past warning`);
      // as the server checks (config.threshold_problem)
      if (name === "cert_days" ? !(crit > 0 && warn <= 3650) : !(warn > 0 && crit <= 100))
        return setProblem(name === "cert_days" ? `${name}: days above 0` : `${name}: percent between 0 and 100`);
    }
    setProblem("");
    const change: AlertSettingsChange = {};
    if (changedLimits) change.thresholds = limits;
    if (changedOff) change.disabled = [...off];
    void run("Save alert settings", () => updateAlertSettings(change).then(() => undefined));
  };
  return (
    <section aria-label="Alerts">
      <h2>Alerts</h2>
      <p className="muted">
        Checked every {s.interval_seconds} seconds{s.enabled ? "" : " (switched off in the config file)"}; cleared alerts
        are kept {s.retention_days} days. Percentages, except certificates: days left. An alert fires once its condition
        has held for the time shown, and clears 2 points below its threshold.
      </p>
      <div className="scroll">
        <table>
          <thead>
            <tr>
              <th>On</th>
              <th>Watches</th>
              <th>Warning at</th>
              <th>Critical at</th>
              <th>Holds for</th>
            </tr>
          </thead>
          <tbody>
            {s.rules.map((r) => (
              <tr key={r.name}>
                <td>
                  <input
                    type="checkbox"
                    aria-label={`${r.name} on`}
                    checked={!off.has(r.name)}
                    onChange={(e) => {
                      const next = new Set(off);
                      if (e.target.checked) next.delete(r.name);
                      else next.add(r.name);
                      setOff(next);
                    }}
                  />
                </td>
                <td>
                  {r.description} <span className="muted">({r.name})</span>
                </td>
                {r.threshold ? (
                  ([0, 1] as const).map((i) => (
                    <td key={i}>
                      <input
                        type="number"
                        className="narrow"
                        aria-label={`${r.threshold} ${i ? "critical" : "warning"}`}
                        value={limits[r.threshold!][i]}
                        onChange={(e) => set(r.threshold!, i, e.target.value)}
                      />
                    </td>
                  ))
                ) : (
                  <td colSpan={2} className="muted">
                    as reported
                  </td>
                )}
                <td>{r.for_seconds ? `${r.for_seconds} s` : "at once"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {problem && <p role="alert">{problem}</p>}
      <p className="actions">
        <button type="button" className="primary" disabled={!changedOff && !changedLimits} onClick={save}>
          Save
        </button>
        <button type="button" onClick={() => setLimits(s.default_thresholds)}>
          Built-in thresholds
        </button>
        {(s.thresholds_saved || s.disabled_saved) && (
          <button
            type="button"
            onClick={() => void run("Alert settings from the config file", () => updateAlertSettings({ reset_thresholds: true, reset_disabled: true }).then(() => undefined))}
          >
            Use the config file
          </button>
        )}
      </p>
    </section>
  );
}

function HostDevices({ s }: { s: HostDeviceSettings }) {
  const { run } = useActions();
  const [text, setText] = useState(s.hidden_paths.join("\n"));
  const [off, setOff] = useState(false);
  const [rebuild, setRebuild] = useState(false);
  const paths = text
    .split("\n")
    .map((p) => p.trim())
    .filter(Boolean);
  const bad = paths.filter((p) => !p.startsWith("/"));
  const changed = paths.join("\n") !== s.hidden_paths.join("\n");
  const b = s.browser;
  const save = (label: string, change: Parameters<typeof updateHostDeviceSettings>[0]) =>
    void run(label, () => updateHostDeviceSettings(change).then(() => undefined));

  return (
    <section aria-label="Host devices">
      <h2>Host devices</h2>
      <p className="muted">
        Who may attach host paths and USB devices is set per user (Users → Projects → host devices). These settings decide
        what project admins with that right can find and attach. Cluster admins aren&apos;t limited by them.
      </p>

      <h3>Folders hidden from project admins</h3>
      <p className="muted">
        One per line, with everything inside. Project admins don&apos;t see them in the folder browser or in the list of
        mounted filesystems, and can&apos;t attach a path inside them. USB disks (<code>/dev/disk/by-id/usb-…</code>) are
        always offered. Like a project&apos;s <code>restricted.devices.disk.paths</code> in LXD, it&apos;s a check on the
        path as typed: a symlink in a folder an instance can write to can still point here, so allow folders instances
        can&apos;t write to. The folder browser resolves links before it checks.{" "}
        <span>Now: {SOURCE[s.hidden_source]}.</span>
      </p>
      <label className="field">
        Hidden folders
        <textarea rows={Math.max(8, paths.length + 1)} value={text} onChange={(e) => setText(e.target.value)} />
      </label>
      {bad.length > 0 && <p role="alert">Not absolute: {bad.join(", ")}</p>}
      {paths.length === 0 && <p role="alert">Nothing hidden: project admins with the right see every folder ordinary users can.</p>}
      <p className="actions">
        <button
          type="button"
          className="primary"
          disabled={!changed || bad.length > 0}
          onClick={() => save("Save hidden folders", { hidden_paths: paths })}
        >
          Save
        </button>
        <button type="button" onClick={() => setText(s.default_hidden.join("\n"))}>
          Built-in list
        </button>
        {s.hidden_source === "app" && (
          <button type="button" onClick={() => save("Hidden folders from the config file", { reset_hidden: true })}>
            Use the config file{s.config_hidden ? "" : " (it sets none: the built-in list)"}
          </button>
        )}
      </p>

      <h3>Folder browser</h3>
      <p className="muted">
        Lets people with the host-devices right browse a member&apos;s folders when adding a host path. LXD can&apos;t list a
        member&apos;s files, so the app keeps a small helper container on each member, in its own project, with the
        member&apos;s <code>/</code> mounted read-only. Helpers start when someone browses and stop after{" "}
        {s.browse_idle_minutes} idle minutes. They see only what an ordinary user on the member could (not{" "}
        <code>/root</code> or private home folders). Every listing is in the audit log. Switching it off deletes the helpers,
        their image and the project. Now: {s.browse ? "on" : "off"} ({SOURCE[s.browse_source]}).
      </p>
      <p className="muted">
        Helpers have no network and aren&apos;t updated in place: one older than {s.browse_refresh_days} days is made again
        from the newest build of <code>{b?.image ?? "its image"}</code> the next time it starts. Rebuild helpers does it now
        for all of them, after a security fix you don&apos;t want to wait for.
      </p>
      {b?.image_warning && <p role="alert">{b.image_warning}.</p>}
      {b?.error && <p role="alert">Helpers unavailable: {b.error}</p>}
      {b && !b.error && (
        <p>
          Project <code>{b.project}</code>:{" "}
          {!b.exists
            ? "not there."
            : !b.ours
              ? "exists but isn't the app's (no user.mcui.role=host-browser): choose another browse_project in the config file."
              : (b.helpers ?? []).length === 0
                ? "there, no helpers yet."
                : null}
        </p>
      )}
      {b && !b.error && (b.helpers ?? []).length > 0 && (
        <div className="scroll">
          <table aria-label="Browser helpers">
            <thead>
              <tr>
                <th>Member</th>
                <th>Status</th>
                <th>Made from</th>
                <th>Made</th>
              </tr>
            </thead>
            <tbody>
              {(b.helpers ?? []).map((h) => (
                <tr key={h.name}>
                  <td>{h.member}</td>
                  <td>{h.status.toLowerCase()}</td>
                  <td>{h.image || <span className="muted">unknown</span>}</td>
                  <td>
                    {h.created_at ? `${age(h.created_at)} ago` : "?"}
                    {h.stale && (
                      <span className="muted">
                        {h.status === "Running" ? " (made again after it stops)" : " (made again when next used)"}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="actions">
        {s.browse ? (
          <button type="button" className="danger" onClick={() => setOff(true)}>
            Switch off and remove the helpers
          </button>
        ) : (
          <button type="button" className="primary" onClick={() => save("Switch the folder browser on", { browse: true })}>
            Switch on
          </button>
        )}
        {b?.ours && (b.helpers ?? []).length > 0 && (
          <button type="button" onClick={() => setRebuild(true)}>
            Rebuild helpers
          </button>
        )}
        {s.browse_source === "app" && (
          <button type="button" onClick={() => save("Folder browser as in the config file", { reset_browse: true })}>
            Use the config file ({s.config_browse ? "on" : "off"})
          </button>
        )}
      </p>
      {rebuild && (
        <Confirm
          title="Rebuild the browser helpers?"
          confirmLabel="Rebuild"
          onCancel={() => setRebuild(false)}
          onConfirm={() => {
            setRebuild(false);
            void run("Rebuild the browser helpers", () => rebuildBrowserHelpers().then(() => undefined));
          }}
        >
          <p>
            The helpers and their image are deleted; each is made again from the image server&apos;s newest build the next time
            someone browses on its member (that needs the image server to be reachable). One in use right now is made again
            once it stops.
          </p>
        </Confirm>
      )}
      {off && (
        <Confirm
          title="Switch the folder browser off?"
          confirmLabel="Switch off"
          danger
          onCancel={() => setOff(false)}
          onConfirm={() => {
            setOff(false);
            save("Switch the folder browser off", { browse: false });
          }}
        >
          <p>
            The helper containers, their image and the project <code>{b?.project ?? "mcui-server"}</code> are deleted.
            Instances keep the host paths already attached.
          </p>
        </Confirm>
      )}
    </section>
  );
}
