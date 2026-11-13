import { useState } from "react";
import {
  backupExportUrl,
  createBackup,
  deleteBackup,
  fetchBackups,
  fetchProject,
  renameBackup,
  type Backup,
  type Compression,
  type InstanceFull,
} from "../../api";
import { restrictionBlocks, useAccess } from "../../access";
import { useActions } from "../../actions";
import Confirm from "../../Confirm";
import { useApi } from "../../useApi";
import { NameDialog } from "./Actions";

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

/** Backups (IN-13, Phase 7a): exported tarballs of the instance, kept by LXD on its member. */
export default function BackupsTab({ inst }: { inst: InstanceFull }) {
  const access = useAccess();
  const { run, version } = useActions();
  const { project, name } = inst;
  const operate = access.can(project, "operate");
  const list = useApi(`backups:${project}/${name}:${version}`, () => fetchBackups(project, name));
  const proj = useApi(`project:${project}`, () => fetchProject(project));
  const blocked = restrictionBlocks(proj.data?.config, "backups");
  const [pending, setPending] = useState<Pending>(null);
  const [backupName, setBackupName] = useState(defaultName);
  const [expires, setExpires] = useState("");
  const [instanceOnly, setInstanceOnly] = useState(false);
  const [optimized, setOptimized] = useState(false);
  const [compression, setCompression] = useState<Compression | "">("");
  const close = () => setPending(null);

  return (
    <section aria-label="Backups">
      <p className="muted">
        A backup is a tarball of the instance (and its snapshots, unless left out) that LXD keeps on the member running
        it, under that member&apos;s <code>storage.backups_volume</code>. Download it to keep a copy elsewhere;
        Instances → Import backup restores one as a new instance.
      </p>
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
                  <td>{b.instance_only ? "left out" : "included"}</td>
                  <td>{b.optimized_storage ? "yes" : "no"}</td>
                  {operate && (
                    <td className="actions">
                      <a className="button" href={backupExportUrl(project, name, b.name)} download>
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
              <input type="checkbox" checked={instanceOnly} onChange={(e) => setInstanceOnly(e.target.checked)} /> Leave
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
                  instance_only: instanceOnly,
                  optimized_storage: optimized,
                  ...(expires ? { expires_at: new Date(expires).toISOString() } : {}),
                  ...(compression ? { compression_algorithm: compression } : {}),
                };
                void run(`Back up ${name} as ${body.name}`, () => createBackup(project, name, body));
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
            void run(`Rename backup ${pending.backup.name}`, () => renameBackup(project, name, pending.backup.name, to));
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
            void run(`Delete backup ${pending.backup.name}`, () => deleteBackup(project, name, pending.backup.name));
          }}
        >
          <p>LXD deletes its copy. Downloaded copies aren&apos;t affected.</p>
        </Confirm>
      )}
    </section>
  );
}
