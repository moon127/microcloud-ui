import { useState } from "react";
import { fetchHostDeviceSettings, updateHostDeviceSettings, type HostDeviceSettings, type SettingSource } from "../../api";
import { useAccess } from "../../access";
import { useActions } from "../../actions";
import Confirm from "../../Confirm";
import { useApi } from "../../useApi";

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
  if (!access.clusterAdmin) return <p role="alert">Settings need the cluster admin role.</p>;
  return (
    <>
      <h1>Settings</h1>
      <p className="muted">
        Saved here, a setting wins over the config file&apos;s <code>[host_devices]</code> section; <strong>Use the config
        file</strong> goes back to it.
      </p>
      {s.error && <p role="alert">{s.error}</p>}
      {!s.data && !s.error && <p role="status">Loading…</p>}
      {s.data && <HostDevices key={JSON.stringify(s.data)} s={s.data} />}
    </>
  );
}

function HostDevices({ s }: { s: HostDeviceSettings }) {
  const { run } = useActions();
  const [text, setText] = useState(s.hidden_paths.join("\n"));
  const [off, setOff] = useState(false);
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
                : (b.helpers ?? []).map((h) => `${h.member}: ${h.status.toLowerCase()}`).join(", ")}
        </p>
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
        {s.browse_source === "app" && (
          <button type="button" onClick={() => save("Folder browser as in the config file", { reset_browse: true })}>
            Use the config file ({s.config_browse ? "on" : "off"})
          </button>
        )}
      </p>
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
