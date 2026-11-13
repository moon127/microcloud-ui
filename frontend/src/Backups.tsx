import { useState, type ReactNode } from "react";
import { fetchProject, type Backup, type Compression, type NewBackup, type Started } from "./api";
import { restrictionBlocks, useAccess } from "./access";
import { useActions } from "./actions";
import Confirm from "./Confirm";
import { NameDialog } from "./pages/instance/Actions";
import { useApi } from "./useApi";

const COMPRESSION: { value: Compression | ""; label: string }[] = [
  { value: "", label: "Server default" },
  { value: "gzip", label: "gzip" },
  { value: "zstd", label: "zstd (faster)" },
  { value: "xz", label: "xz (smaller, slow)" },
  { value: "bzip2", label: "bzip2" },
  { value: "none", label: "None" },
];

type Pending = { kind: "rename" | "delete"; backup: Backup } | null;

function defaultName(): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `backup-${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}`;
}

const never = (t?: string) => !t || t.startsWith("0001");

/** How a page's backups are reached: an instance's or a custom volume's. */
export interface BackupsSource {
  key: string;
  list: () => Promise<Backup[]>;
  create: (body: NewBackup) => Promise<Started>;
  rename: (backup: string, to: string) => Promise<Started>;
  remove: (backup: string) => Promise<Started>;
  exportUrl: (backup: string) => string;
}

/** Backups of an instance (IN-13) or a volume (ST-4), Phase 7: tarballs LXD keeps on a member. */
export default function Backups({
  project,
  name,
  source,
  intro,
  only,
}: {
  project: string;
  name: string;
  source: BackupsSource;
  intro: ReactNode;
  /** what "leave out snapshots" sets: instance_only or volume_only */
  only: "instance_only" | "volume_only";
}) {
  const access = useAccess();
  const { run, version } = useActions();
  const operate = access.can(project, "operate");
  const list = useApi(`${source.key}:${version}`, source.list);
  const proj = useApi(`project:${project}`, () => fetchProject(project));
  const blocked = restrictionBlocks(proj.data?.config, "backups");
  const [pending, setPending] = useState<Pending>(null);
  const [backupName, setBackupName] = useState(defaultName);
  const [expires, setExpires] = useState("");
  const [snapshotsOut, setSnapshotsOut] = useState(false);
  const [optimized, setOptimized] = useState(false);
  const [compression, setCompression] = useState<Compression | "">("");
  const close = () => setPending(null);

  return (
    <section aria-label="Backups">
      <p className="muted">{intro}</p>
      {list.error && <p role="alert">{list.error}</p>}
      {list.data && list.data.length === 0 && <p>No backups.</p>}
      {list.data && list.data.length > 0 && (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Made</th>
                <th>Expires</th>
                <th>Snapshots</th>
                <th>Optimized</th>
                {operate && <th />}
              </tr>
            </thead>
            <tbody>
              {list.data.map((b) => (
                <tr key={b.name}>
                  <td>{b.name}</td>
                  <td>{new Date(b.created_at).toLocaleString()}</td>
                  <td>{never(b.expires_at) ? "never" : new Date(b.expires_at!).toLocaleString()}</td>
                  <td>{b[only] ? "left out" : "included"}</td>
                  <td>{b.optimized_storage ? "yes" : "no"}</td>
                  {operate && (
                    <td className="actions">
                      <a className="button" href={source.exportUrl(b.name)} download>
                        Download
                      </a>
                      <button type="button" onClick={() => setPending({ kind: "rename", backup: b })}>
                        Rename
                      </button>
                      <button type="button" className="danger" onClick={() => setPending({ kind: "delete", backup: b })}>
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

      {operate && blocked && (
        <p role="note">
          Project {project} is restricted and doesn&apos;t allow backups. A cluster admin can allow them by setting{" "}
          <code>restricted.backups=allow</code> on the project.
        </p>
      )}
      {operate && !blocked && (
        <>
          <h2>Make a backup</h2>
          <div className="form-row">
            <label className="field">
              Name
              <input value={backupName} onChange={(e) => setBackupName(e.target.value)} />
            </label>
            <label className="field">
              Expires (optional)
              <input type="datetime-local" value={expires} onChange={(e) => setExpires(e.target.value)} />
            </label>
            <label className="field">
              Compression
              <select value={compression} onChange={(e) => setCompression(e.target.value as Compression | "")}>
                {COMPRESSION.map((c) => (
                  <option key={c.value} value={c.value}>
                    {c.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <input type="checkbox" checked={snapshotsOut} onChange={(e) => setSnapshotsOut(e.target.checked)} /> Leave
              out snapshots
            </label>
            <label>
              <input type="checkbox" checked={optimized} onChange={(e) => setOptimized(e.target.checked)} /> Optimized
              storage (faster, but restores only onto the same storage driver)
            </label>
            <button
              type="button"
              className="primary"
              disabled={!backupName.trim()}
              onClick={() => {
                const body = {
                  name: backupName.trim(),
                  [only]: snapshotsOut,
                  optimized_storage: optimized,
                  ...(expires ? { expires_at: new Date(expires).toISOString() } : {}),
                  ...(compression ? { compression_algorithm: compression } : {}),
                };
                void run(`Back up ${name} as ${body.name}`, () => source.create(body));
                setBackupName(defaultName());
              }}
            >
              Make backup
            </button>
          </div>
        </>
      )}

      {pending?.kind === "rename" && (
        <NameDialog
          title={`Rename backup ${pending.backup.name}`}
          label="New name"
          initial={pending.backup.name}
          onCancel={close}
          onSubmit={(to) => {
            close();
            void run(`Rename backup ${pending.backup.name}`, () => source.rename(pending.backup.name, to));
          }}
        />
      )}
      {pending?.kind === "delete" && (
        <Confirm
          title={`Delete backup ${pending.backup.name}?`}
          confirmLabel="Delete"
          danger
          onCancel={close}
          onConfirm={() => {
            close();
            void run(`Delete backup ${pending.backup.name}`, () => source.remove(pending.backup.name));
          }}
        >
          <p>LXD deletes its copy. Downloaded copies aren&apos;t affected.</p>
        </Confirm>
      )}
    </section>
  );
}
