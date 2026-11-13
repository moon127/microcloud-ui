import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import {
  attachVolume,
  createVolumeBackup,
  createVolumeSnapshot,
  deleteVolume,
  deleteVolumeBackup,
  deleteVolumeSnapshot,
  detachVolume,
  fetchInstance,
  fetchInstances,
  fetchVolume,
  fetchVolumeBackups,
  fetchVolumeSnapshots,
  renameVolume,
  renameVolumeBackup,
  renameVolumeSnapshot,
  restoreVolumeSnapshot,
  updateVolume,
  volumeBackupExportUrl,
  type VolumeDetail,
} from "../../api";
import { useActions } from "../../actions";
import Backups from "../../Backups";
import CopyMoveVolume from "./CopyMoveVolume";
import Confirm from "../../Confirm";
import { formatBytes } from "../../format";
import { InstanceLink, MemberLink, UsedBy } from "../../links";
import { useApi } from "../../useApi";
import YamlEditor from "../../YamlEditor";
import { NameDialog } from "../instance/Actions";
import { asConfig } from "../instance/state";
import { poolPath, volumePath } from "./paths";
import { useAccess } from "../../access";

const FORM_KEYS = [
  { key: "size", label: "Size (raise to grow it)", placeholder: "pool default" },
  { key: "snapshots.schedule", label: "Snapshot schedule (cron or @daily)", placeholder: "none" },
  { key: "snapshots.expiry", label: "Snapshots expire after", placeholder: "never (e.g. 2w)" },
  { key: "snapshots.pattern", label: "Snapshot name pattern", placeholder: "snap%d" },
];

interface Draft {
  description: string;
  config: Record<string, string>;
}

type Dialog = { kind: "rename" | "delete" | "attach" | "snapshot" | "copy" | "move" } | { kind: "snap-rename" | "snap-delete" | "snap-restore"; snap: string };

export default function VolumePage() {
  const { project = "default", pool = "", name = "" } = useParams();
  const [params] = useSearchParams();
  const target = params.get("member") ?? undefined;
  const access = useAccess();
  const { run, version } = useActions();
  const navigate = useNavigate();
  const vol = useApi(`volume:${project}/${pool}/${name}:${target}:${version}`, () => fetchVolume(project, pool, name, target));
  const snaps = useApi(`volume-snaps:${project}/${pool}/${name}:${target}:${version}`, () =>
    fetchVolumeSnapshots(project, pool, name, target),
  );
  const [draft, setDraft] = useState<Draft | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const admin = access.can(project, "manage");
  const operate = access.can(project, "operate"); // snapshots and backups
  const v = vol.data;

  // take the loaded values into the form as they arrive, during render rather than in an
  // effect, so the form never shows a render's worth of stale values
  const [shown, setShown] = useState<unknown>(null);
  if (v !== shown) {
    setShown(v);
    if (v) setDraft({ description: v.description, config: v.config });
  }

  const other = draft ? Object.fromEntries(Object.entries(draft.config).filter(([k]) => !FORM_KEYS.some((f) => f.key === k))) : {};
  const setKey = (k: string, val: string) =>
    setDraft((d) => {
      if (!d) return d;
      const config = { ...d.config };
      if (val.trim()) config[k] = val.trim();
      else delete config[k];
      return { ...d, config };
    });
  const short = (s: string) => s.split("/").pop() ?? s;
  const close = () => setDialog(null);

  return (
    <>
      <h1>
        Volume {name}{" "}
        <span className="muted">
          in {project} · <Link to={poolPath(pool)}>{pool}</Link>
        </span>
      </h1>
      {vol.error && <p role="alert">{vol.error}</p>}
      {v && draft && (
        <>
          {admin && (
            <div className="actions" role="toolbar" aria-label="Volume actions">
              <button
                type="button"
                className="primary"
                disabled={problem !== null}
                onClick={() => void run(`Save volume ${name}`, () => updateVolume(project, pool, name, draft, target))}
              >
                Save
              </button>
              <button type="button" onClick={() => setDialog({ kind: "attach" })}>
                Attach…
              </button>
              <button type="button" onClick={() => setDialog({ kind: "rename" })}>
                Rename
              </button>
              <button type="button" onClick={() => setDialog({ kind: "copy" })}>
                Copy…
              </button>
              <button type="button" onClick={() => setDialog({ kind: "move" })}>
                Move…
              </button>
              <button type="button" className="danger" onClick={() => setDialog({ kind: "delete" })}>
                Delete
              </button>
            </div>
          )}
          <div className="hardware">
            <dl>
              <dt>Content</dt>
              <dd>{v.content_type}</dd>
              <dt>Member</dt>
              <dd>{target ? <MemberLink name={target} /> : "–"}</dd>
              <dt>Used</dt>
              <dd>
                {v.state?.usage ? formatBytes(v.state.usage.used) : "–"}
                {v.config.size ? ` of ${v.config.size}` : ""}
              </dd>
              {v.created_at && (
                <>
                  <dt>Created</dt>
                  <dd>{new Date(v.created_at).toLocaleString()}</dd>
                </>
              )}
            </dl>
          </div>
          <h2>Settings</h2>
          <fieldset disabled={!admin}>
            <label className="field">
              Description
              <input value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} />
            </label>
            <div className="form-row">
              {FORM_KEYS.map((f) => (
                <label key={f.key} className="field">
                  {f.label}
                  <input placeholder={f.placeholder} value={draft.config[f.key] ?? ""} onChange={(e) => setKey(f.key, e.target.value)} />
                </label>
              ))}
            </div>
          </fieldset>
          <YamlEditor
            label="Other config (YAML)"
            rows={5}
            value={other}
            readOnly={!admin}
            onChange={(val) => {
              const c = asConfig(val);
              setProblem(typeof c === "string" ? c : null);
              if (typeof c !== "string")
                setDraft((d) => {
                  if (!d) return d;
                  const kept = Object.fromEntries(Object.entries(d.config).filter(([k]) => FORM_KEYS.some((f) => f.key === k)));
                  return { ...d, config: { ...c, ...kept } };
                });
            }}
          />
          {problem && <p role="alert">{problem}</p>}
          <Attached project={project} pool={pool} name={name} v={v} admin={admin} />
          <h2>Snapshots</h2>
          {operate && (
            <p>
              <button type="button" onClick={() => setDialog({ kind: "snapshot" })}>
                Create snapshot
              </button>
            </p>
          )}
          {snaps.error && <p role="alert">{snaps.error}</p>}
          {snaps.data && snaps.data.length === 0 && <p>None.</p>}
          {snaps.data && snaps.data.length > 0 && (
            <div className="scroll">
              <table>
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Created</th>
                    <th>Expires</th>
                    {operate && <th />}
                  </tr>
                </thead>
                <tbody>
                  {snaps.data.map((s) => {
                    const sn = short(s.name);
                    const expires = s.expires_at && !s.expires_at.startsWith("0001") ? new Date(s.expires_at).toLocaleString() : "never";
                    return (
                      <tr key={s.name}>
                        <td>{sn}</td>
                        <td>{new Date(s.created_at).toLocaleString()}</td>
                        <td>{expires}</td>
                        {operate && (
                          <td className="actions">
                            <button type="button" onClick={() => setDialog({ kind: "snap-restore", snap: sn })}>
                              Restore
                            </button>
                            <button type="button" onClick={() => setDialog({ kind: "snap-rename", snap: sn })}>
                              Rename
                            </button>
                            <button type="button" className="danger" onClick={() => setDialog({ kind: "snap-delete", snap: sn })}>
                              Delete
                            </button>
                          </td>
                        )}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          <h2>Backups</h2>
          <Backups
            project={project}
            name={name}
            only="volume_only"
            source={{
              key: `volume-backups:${project}/${pool}/${name}:${target}`,
              list: () => fetchVolumeBackups(project, pool, name, target),
              create: (body) => createVolumeBackup(project, pool, name, body, target),
              rename: (backup, to) => renameVolumeBackup(project, pool, name, backup, to, target),
              remove: (backup) => deleteVolumeBackup(project, pool, name, backup, target),
              exportUrl: (backup) => volumeBackupExportUrl(project, pool, name, backup, target),
            }}
            intro={
              <>
                A tarball of the volume (and its snapshots, unless left out) that LXD keeps on a member, under its{" "}
                <code>storage.backups_volume</code>. Volumes → Import backup restores one as a new volume.
              </>
            }
          />
        </>
      )}
      {(dialog?.kind === "copy" || dialog?.kind === "move") && v && (
        <CopyMoveVolume kind={dialog.kind} project={project} pool={pool} v={v} target={target} onClose={close} />
      )}
      {dialog?.kind === "rename" && (
        <NameDialog
          title={`Rename volume ${name}`}
          label="New name"
          initial={name}
          onCancel={close}
          onSubmit={(to) => {
            close();
            void run(`Rename volume ${name}`, () => renameVolume(project, pool, name, to, target)).then(
              (ok) => ok && navigate(volumePath(project, pool, to, target)),
            );
          }}
        />
      )}
      {dialog?.kind === "delete" && (
        <Confirm
          title={`Delete volume ${name}?`}
          confirmLabel="Delete"
          danger
          onCancel={close}
          onConfirm={() => {
            close();
            void run(`Delete volume ${name}`, () => deleteVolume(project, pool, name, target)).then(
              (ok) => ok && navigate("/storage/volumes"),
            );
          }}
        >
          <p>Its data and snapshots are lost. Volumes attached to instances can't be deleted.</p>
        </Confirm>
      )}
      {dialog?.kind === "snapshot" && (
        <SnapshotDialog
          initial={`snap${(snaps.data ?? []).length}`}
          onCancel={close}
          onSubmit={(body) => {
            close();
            void run(`Snapshot ${name}`, () => createVolumeSnapshot(project, pool, name, body, target));
          }}
        />
      )}
      {dialog?.kind === "snap-rename" && (
        <NameDialog
          title={`Rename snapshot ${dialog.snap}`}
          label="New name"
          initial={dialog.snap}
          onCancel={close}
          onSubmit={(to) => {
            close();
            void run(`Rename snapshot ${dialog.snap}`, () => renameVolumeSnapshot(project, pool, name, dialog.snap, to, target));
          }}
        />
      )}
      {dialog?.kind === "snap-restore" && (
        <Confirm
          title={`Restore ${name} to ${dialog.snap}?`}
          confirmLabel="Restore"
          danger
          onCancel={close}
          onConfirm={() => {
            close();
            void run(`Restore ${name} to ${dialog.snap}`, () => restoreVolumeSnapshot(project, pool, name, dialog.snap, target));
          }}
        >
          <p>
            Changes since the snapshot are lost. Detach the volume (or stop its instances) first. On ZFS only the latest snapshot can be
            restored, unless the volume has <code>zfs.remove_snapshots: true</code> (which deletes the later ones).
          </p>
        </Confirm>
      )}
      {dialog?.kind === "snap-delete" && (
        <Confirm
          title={`Delete snapshot ${dialog.snap}?`}
          confirmLabel="Delete"
          danger
          onCancel={close}
          onConfirm={() => {
            close();
            void run(`Delete snapshot ${dialog.snap}`, () => deleteVolumeSnapshot(project, pool, name, dialog.snap, target));
          }}
        />
      )}
      {dialog?.kind === "attach" && v && <AttachDialog project={project} pool={pool} v={v} onClose={close} />}
    </>
  );
}

function SnapshotDialog({
  initial,
  onCancel,
  onSubmit,
}: {
  initial: string;
  onCancel: () => void;
  onSubmit: (body: { name: string; expires_at?: string }) => void;
}) {
  const [name, setName] = useState(initial);
  const [expires, setExpires] = useState("");
  return (
    <Confirm
      title="Create snapshot"
      confirmLabel="Create"
      onCancel={onCancel}
      onConfirm={() => name.trim() && onSubmit({ name: name.trim(), ...(expires ? { expires_at: new Date(expires).toISOString() } : {}) })}
    >
      <label className="field">
        Name
        <input value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label className="field">
        Expires (optional)
        <input type="datetime-local" value={expires} onChange={(e) => setExpires(e.target.value)} />
      </label>
    </Confirm>
  );
}

/** Instances using the volume; detaching needs the device name, read from the instance. */
function Attached({ project, pool, name, v, admin }: { project: string; pool: string; name: string; v: VolumeDetail; admin: boolean }) {
  const { run } = useActions();
  const users = v.used_by ?? [];
  const insts = users
    .map((u) => new URL(u, "http://x"))
    .filter((u) => /^\/1\.0\/instances\/[^/]+$/.test(u.pathname))
    .map((u) => ({ project: u.searchParams.get("project") ?? project, name: decodeURIComponent(u.pathname.split("/").pop()!) }));
  const others = users.filter((u) => !/^\/1\.0\/instances\/[^/?]+(\?|$)/.test(u));
  const devices = useApi(`volume-devices:${project}/${pool}/${name}:${users.join(",")}`, () =>
    Promise.all(
      insts.map((i) =>
        fetchInstance(i.project, i.name).then((full) => {
          const own = Object.entries(full.devices ?? {}).find(([, d]) => d.type === "disk" && d.pool === pool && d.source === name);
          return [i.name, own?.[0] ?? null] as const;
        }),
      ),
    ),
  );
  return (
    <>
      <h2>Used by</h2>
      {users.length === 0 && <p>Not attached to anything.</p>}
      <ul>
        {insts.map((i) => {
          const dev = devices.data?.find(([n]) => n === i.name)?.[1];
          return (
            <li key={i.name}>
              <InstanceLink project={i.project} name={i.name} />
              {dev && <span className="muted"> as {dev}</span>}
              {devices.data && dev === null && <span className="muted"> (through a profile)</span>}
              {admin && dev && (
                <>
                  {" "}
                  <button
                    type="button"
                    onClick={() => void run(`Detach ${name} from ${i.name}`, () => detachVolume(project, pool, name, { instance: i.name, device: dev }))}
                  >
                    Detach
                  </button>
                </>
              )}
            </li>
          );
        })}
        {others.map((u) => (
          <li key={u}>
            <UsedBy url={u} />
          </li>
        ))}
      </ul>
    </>
  );
}

function AttachDialog({ project, pool, v, onClose }: { project: string; pool: string; v: VolumeDetail; onClose: () => void }) {
  const { run } = useActions();
  const insts = useApi("instances", fetchInstances);
  const iso = v.content_type === "iso"; // a CD, for VMs only
  const mine = (insts.data ?? []).filter((i) => i.project === project && (!iso || i.type === "virtual-machine"));
  const [instance, setInstance] = useState("");
  const [device, setDevice] = useState(v.name);
  const [path, setPath] = useState(`/mnt/${v.name}`);
  const [bootFirst, setBootFirst] = useState(true);
  const [problem, setProblem] = useState<string | null>(null);
  const block = v.content_type === "block" || iso;
  return (
    <Confirm
      title={`Attach ${v.name}`}
      confirmLabel="Attach"
      onCancel={onClose}
      onConfirm={() => {
        const inst = instance || mine[0]?.name;
        if (!inst) return setProblem(iso ? "There is no VM in this project to attach it to" : "Choose an instance");
        if (!device.trim()) return setProblem("Enter a device name");
        if (!block && !path.startsWith("/")) return setProblem("The mount path must be absolute");
        onClose();
        void run(`Attach ${v.name} to ${inst}`, () =>
          attachVolume(project, pool, v.name, {
            instance: inst,
            device: device.trim(),
            ...(block ? {} : { path }),
            // LXD boots the highest boot.priority first; the root disk has none
            ...(iso && bootFirst ? { boot_priority: 10 } : {}),
          }),
        );
      }}
    >
      <label className="field">
        Instance
        <select value={instance || mine[0]?.name || ""} onChange={(e) => setInstance(e.target.value)}>
          {mine.map((i) => (
            <option key={i.name} value={i.name}>
              {i.name} ({i.type}, {i.status})
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        Device name
        <input value={device} onChange={(e) => setDevice(e.target.value)} />
      </label>
      {iso && (
        <>
          <p className="muted">An ISO is attached to a VM as a CD. The VM sees it after a restart.</p>
          <label>
            <input type="checkbox" checked={bootFirst} onChange={(e) => setBootFirst(e.target.checked)} /> Boot from it
            first (to install from it)
          </label>
        </>
      )}
      {iso ? null : block ? (
        <p className="muted">Block volumes are attached to VMs as a disk, without a mount path.</p>
      ) : (
        <label className="field">
          Mount path
          <input value={path} onChange={(e) => setPath(e.target.value)} />
        </label>
      )}
      {problem && <p role="alert">{problem}</p>}
    </Confirm>
  );
}
