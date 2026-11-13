import { useState } from "react";
import { fetchMembers, fetchStoragePools, importVolumeBackup, type PoolDriver } from "../../api";
import { useActions } from "../../actions";
import Confirm from "../../Confirm";
import { useApi } from "../../useApi";
import { LOCAL } from "./request";

const NAME = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;

/** Volumes → Import backup: a volume backup tarball becomes a new custom volume (Phase 7b). */
export default function ImportVolumeBackup({ project, onClose }: { project: string; onClose: () => void }) {
  const { run } = useActions();
  const pools = useApi("pools", fetchStoragePools);
  const members = useApi("members", fetchMembers);
  const [file, setFile] = useState<File | null>(null);
  const [pool, setPool] = useState("");
  const [name, setName] = useState("");
  const [target, setTarget] = useState("");
  const [problem, setProblem] = useState("");
  const usable = (pools.data ?? []).filter((p) => p.driver !== "cephobject");
  const chosen = usable.find((p) => p.name === (pool || usable[0]?.name));
  const local = !!chosen && LOCAL.has(chosen.driver as PoolDriver);
  const many = (members.data ?? []).length > 1;

  return (
    <Confirm
      title={`Import a volume backup into ${project}`}
      confirmLabel="Import"
      onCancel={onClose}
      onConfirm={() => {
        if (!file) return setProblem("Choose a backup file.");
        if (!chosen) return setProblem("Choose a pool.");
        const to = name.trim();
        if (to && !NAME.test(to)) return setProblem("Names start with a letter or digit: letters, digits, . _ -");
        onClose();
        const dest = { project, pool: chosen.name, name: to, target: local && many ? target : "" };
        void run(`Import ${file.name} into ${chosen.name}`, async (progress) => {
          const st = await importVolumeBackup(file, dest, (f) =>
            progress(f < 1 ? `${Math.floor(f * 100)}% uploaded` : "uploaded; LXD is unpacking it"),
          );
          progress("LXD is creating the volume");
          return st;
        });
      }}
    >
      <p className="muted">
        A tarball made by a volume backup here or by <code>lxc storage volume export</code>. It streams to LXD, so keep
        this tab open until the upload finishes.
      </p>
      <label className="field">
        Backup file
        <input type="file" accept=".tar,.gz,.tgz,.zst,.xz,.bz2" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
      </label>
      <label className="field">
        Pool
        <select value={chosen?.name ?? ""} onChange={(e) => setPool(e.target.value)}>
          {usable.map((p) => (
            <option key={p.name} value={p.name}>
              {p.name} ({p.driver})
            </option>
          ))}
        </select>
      </label>
      {local && many && (
        <label className="field">
          Member
          <select value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="">The one answering the request</option>
            {(members.data ?? []).map((m) => (
              <option key={m.name}>{m.name}</option>
            ))}
          </select>
        </label>
      )}
      <label className="field">
        Name (optional; the backup&apos;s own if empty)
        <input value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      {problem && <p role="alert">{problem}</p>}
    </Confirm>
  );
}
