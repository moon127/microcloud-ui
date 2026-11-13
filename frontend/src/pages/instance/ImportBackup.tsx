import { useState } from "react";
import { fetchProjects, fetchStoragePools, importBackup } from "../../api";
import { importRefused, useAccess } from "../../access";
import { useActions } from "../../actions";
import Confirm from "../../Confirm";
import { useApi } from "../../useApi";

const NAME = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;

/** Instances → Import backup: a backup tarball becomes a new instance (Phase 7a). */
export default function ImportBackup({ project: initial, onClose }: { project: string; onClose: () => void }) {
  const access = useAccess();
  const { run } = useActions();
  const projects = useApi("projects", fetchProjects);
  const pools = useApi("storage-pools", fetchStoragePools);
  const [file, setFile] = useState<File | null>(null);
  const [project, setProject] = useState(initial);
  const [name, setName] = useState("");
  const [pool, setPool] = useState("");
  const [problem, setProblem] = useState("");
  const choices = (projects.data ?? [{ name: initial }]).filter((p) => access.can(p.name, "manage"));
  const config = projects.data?.find((p) => p.name === project)?.config;

  return (
    <Confirm
      title="Import a backup"
      confirmLabel="Import"
      onCancel={onClose}
      onConfirm={() => {
        if (!file) return setProblem("Choose a backup file.");
        const to = name.trim();
        if (to && !NAME.test(to)) return setProblem("Names start with a letter or digit: letters, digits, . _ -");
        onClose();
        void run(`Import ${file.name} into ${project}`, async (progress) => {
          const st = await importBackup(file, { project, name: to, pool }, (f) =>
            progress(f < 1 ? `${Math.floor(f * 100)}% uploaded` : "uploaded; LXD is unpacking it"),
          );
          progress("LXD is creating the instance");
          return st;
        });
      }}
    >
      <p className="muted">
        A tarball made by a backup here or by <code>lxc export</code>. It streams to LXD, so keep this tab open until
        the upload finishes.
      </p>
      <label className="field">
        Backup file
        <input type="file" accept=".tar,.gz,.tgz,.zst,.xz,.bz2" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
      </label>
      <label className="field">
        Project
        <select value={project} onChange={(e) => setProject(e.target.value)}>
          {choices.map((p) => (
            <option key={p.name}>{p.name}</option>
          ))}
        </select>
      </label>
      <label className="field">
        Name (optional; the backup&apos;s own if empty)
        <input value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label className="field">
        Storage pool
        <select value={pool} onChange={(e) => setPool(e.target.value)}>
          <option value="">The backup&apos;s own</option>
          {(pools.data ?? []).map((p) => (
            <option key={p.name}>{p.name}</option>
          ))}
        </select>
      </label>
      {importRefused(config) && (
        <p role="note">
          {project} has limits or restrictions, so LXD will probably refuse the import: it counts the backup&apos;s own{" "}
          <code>volatile.*</code> keys as low-level options. It works with{" "}
          <code>restricted.containers.lowlevel=allow</code>, which is harmless in an unrestricted project but in a
          restricted one also lets its users set <code>raw.lxc</code> and reach the host. Instead, import into an
          unrestricted project without limits and move the instance across (stopped); LXD still checks it there.
        </p>
      )}
      {problem && <p role="alert">{problem}</p>}
    </Confirm>
  );
}
