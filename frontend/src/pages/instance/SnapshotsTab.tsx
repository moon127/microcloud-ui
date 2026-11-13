import { useState } from "react";
import {
  copyInstance,
  createSnapshot,
  deleteSnapshot,
  fetchServer,
  patchInstanceConfig,
  publishInstance,
  renameSnapshot,
  restoreSnapshot,
  type InstanceFull,
  type Snapshot,
} from "../../api";
import { useActions } from "../../actions";
import Confirm from "../../Confirm";
import { useApi } from "../../useApi";
import { NameDialog } from "./Actions";
import { useAccess } from "../../access";

type Pending = { kind: "restore" | "delete" | "rename" | "clone" | "publish"; snap: Snapshot } | null;

function defaultName(): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `snap-${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}`;
}

const SCHEDULE = [
  { key: "snapshots.schedule", label: "Schedule", hint: "cron or @hourly, @daily, @weekly; empty = none" },
  { key: "snapshots.expiry", label: "Expire after", hint: "e.g. 7d, 4w, 1M" },
  { key: "snapshots.pattern", label: "Name pattern", hint: "default snap%d" },
];

export default function SnapshotsTab({ inst }: { inst: InstanceFull }) {
  const access = useAccess();
  const { run } = useActions();
  const admin = access.can(inst.project, "operate"); // snapshots
  const manage = access.can(inst.project, "manage"); // a new instance or an image from one
  const [pending, setPending] = useState<Pending>(null);
  const [name, setName] = useState(defaultName);
  const [expires, setExpires] = useState("");
  const [stateful, setStateful] = useState(false);
  // LXD 6.6+: snapshot and restore the volumes attached only to this instance too
  const multiVolume = !!useApi("server", fetchServer).data?.features.snapshot_volumes;
  const [withVolumes, setWithVolumes] = useState(false);
  const [restoreVolumes, setRestoreVolumes] = useState(false);
  const [schedule, setSchedule] = useState(() =>
    Object.fromEntries(SCHEDULE.map((s) => [s.key, inst.config[s.key] ?? ""])),
  );
  const { project } = inst;
  const snaps = [...(inst.snapshots ?? [])].sort((a, b) => b.created_at.localeCompare(a.created_at));
  const close = () => setPending(null);
  const label = (s: Snapshot) => `${inst.name}/${s.name}`;

  return (
    <section aria-label="Snapshots">
      {snaps.length === 0 ? (
        <p>No snapshots.</p>
      ) : (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Taken</th>
                <th>Expires</th>
                <th>Stateful</th>
                {admin && <th />}
              </tr>
            </thead>
            <tbody>
              {snaps.map((s) => (
                <tr key={s.name}>
                  <td>{s.name}</td>
                  <td>{new Date(s.created_at).toLocaleString()}</td>
                  <td>{s.expires_at && !s.expires_at.startsWith("0001") ? new Date(s.expires_at).toLocaleString() : "never"}</td>
                  <td>{s.stateful ? "yes" : "no"}</td>
                  {admin && (
                    <td className="actions">
                      <button type="button" onClick={() => setPending({ kind: "restore", snap: s })}>
                        Restore
                      </button>
                      <button type="button" onClick={() => setPending({ kind: "rename", snap: s })}>
                        Rename
                      </button>
                      {manage && (
                        <>
                          <button type="button" onClick={() => setPending({ kind: "clone", snap: s })}>
                            New instance
                          </button>
                          <button type="button" onClick={() => setPending({ kind: "publish", snap: s })}>
                            Publish
                          </button>
                        </>
                      )}
                      <button type="button" className="danger" onClick={() => setPending({ kind: "delete", snap: s })}>
                        Delete
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {admin && (
        <>
          <h2>Take a snapshot</h2>
          <div className="form-row">
            <label className="field">
              Name
              <input value={name} onChange={(e) => setName(e.target.value)} />
            </label>
            <label className="field">
              Expires (optional)
              <input type="datetime-local" value={expires} onChange={(e) => setExpires(e.target.value)} />
            </label>
            <label>
              <input type="checkbox" checked={stateful} onChange={(e) => setStateful(e.target.checked)} /> Include memory
              (stateful)
            </label>
            {multiVolume && (
              <label>
                <input type="checkbox" checked={withVolumes} onChange={(e) => setWithVolumes(e.target.checked)} /> Include
                volumes attached only to this instance
              </label>
            )}
            <button
              type="button"
              className="primary"
              disabled={!name.trim()}
              onClick={() => {
                const opts = {
                  name: name.trim(),
                  stateful,
                  ...(expires ? { expires_at: new Date(expires).toISOString() } : {}),
                  ...(multiVolume && withVolumes ? { disk_volumes_mode: "all-exclusive" as const } : {}),
                };
                void run(`Snapshot ${inst.name}/${opts.name}`, () => createSnapshot(project, inst.name, opts));
                setName(defaultName());
              }}
            >
              Take snapshot
            </button>
          </div>

          <h2>Automatic snapshots</h2>
          <div className="form-row">
            {SCHEDULE.map((s) => (
              <label key={s.key} className="field">
                {s.label} <span className="muted">({s.hint})</span>
                <input value={schedule[s.key]} onChange={(e) => setSchedule({ ...schedule, [s.key]: e.target.value })} />
              </label>
            ))}
            <button
              type="button"
              onClick={() => void run(`Save ${inst.name} snapshot schedule`, () => patchInstanceConfig(project, inst.name, schedule))}
            >
              Save schedule
            </button>
          </div>
        </>
      )}

      {pending?.kind === "restore" && (
        <Confirm
          title={`Restore ${label(pending.snap)}?`}
          confirmLabel="Restore"
          danger
          onCancel={close}
          onConfirm={() => {
            const s = pending.snap;
            close();
            void run(`Restore ${label(s)}`, () => restoreSnapshot(project, inst.name, s.name, multiVolume && restoreVolumes));
          }}
        >
          <p>The instance's current disk is replaced by the snapshot. Changes since then are lost.</p>
          {multiVolume && (
            <label>
              <input type="checkbox" checked={restoreVolumes} onChange={(e) => setRestoreVolumes(e.target.checked)} /> Also
              restore the volumes attached only to this instance, from the snapshots taken with it
            </label>
          )}
        </Confirm>
      )}
      {pending?.kind === "delete" && (
        <Confirm
          title={`Delete ${label(pending.snap)}?`}
          confirmLabel="Delete"
          danger
          onCancel={close}
          onConfirm={() => {
            const s = pending.snap;
            close();
            void run(`Delete ${label(s)}`, () => deleteSnapshot(project, inst.name, s.name));
          }}
        >
          {multiVolume && (
            <p className="muted">
              Volume snapshots taken with it are kept; delete them on each volume&apos;s page.
            </p>
          )}
        </Confirm>
      )}
      {pending?.kind === "rename" && (
        <NameDialog
          title={`Rename ${label(pending.snap)}`}
          label="New name"
          initial={pending.snap.name}
          onCancel={close}
          onSubmit={(to) => {
            const s = pending.snap;
            close();
            void run(`Rename ${label(s)} to ${to}`, () => renameSnapshot(project, inst.name, s.name, to));
          }}
        />
      )}
      {pending?.kind === "clone" && (
        <NameDialog
          title={`New instance from ${label(pending.snap)}`}
          label="Instance name"
          initial={`${inst.name}-${pending.snap.name}`}
          onCancel={close}
          onSubmit={(to) => {
            const s = pending.snap;
            close();
            void run(`Create ${to} from ${label(s)}`, () => copyInstance(project, inst.name, { name: to, snapshot: s.name }));
          }}
        />
      )}
      {pending?.kind === "publish" && (
        <NameDialog
          title={`Publish ${label(pending.snap)} as an image`}
          label="Alias (optional)"
          optional
          onCancel={close}
          onSubmit={(alias) => {
            const s = pending.snap;
            close();
            void run(`Publish ${label(s)}`, () =>
              publishInstance(project, inst.name, { snapshot: s.name, ...(alias ? { alias } : {}) }),
            );
          }}
        />
      )}
    </section>
  );
}
