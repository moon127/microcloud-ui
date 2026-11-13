import { useState } from "react";
import { fetchMembers, fetchStoragePools, importVolumeBackup, uploadIso, type PoolDriver } from "../../api";
import { useActions } from "../../actions";
import Confirm from "../../Confirm";
import { useApi } from "../../useApi";
import { isoName, LOCAL } from "./request";

const NAME = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;

const KINDS = {
  backup: {
    title: "Import a volume backup",
    file: "Backup file",
    accept: ".tar,.gz,.tgz,.zst,.xz,.bz2",
    send: (...a: Parameters<typeof importVolumeBackup>) => importVolumeBackup(...a),
    done: "LXD is creating the volume",
  },
  iso: {
    title: "Upload an ISO",
    file: "ISO image",
    accept: ".iso",
    send: (...a: Parameters<typeof uploadIso>) => uploadIso(...a),
    done: "LXD is storing it",
  },
};

/** Volumes → Import backup (Phase 7b) or Upload ISO (Phase 7d): a file streamed to LXD
 * becomes a new custom volume. */
export default function UploadVolume({
  kind,
  project,
  onClose,
}: {
  kind: "backup" | "iso";
  project: string;
  onClose: () => void;
}) {
  const k = KINDS[kind];
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
      title={`${k.title} into ${project}`}
      confirmLabel={kind === "iso" ? "Upload" : "Import"}
      onCancel={onClose}
      onConfirm={() => {
        if (!file) return setProblem(`Choose ${kind === "iso" ? "an ISO image" : "a backup file"}.`);
        if (!chosen) return setProblem("Choose a pool.");
        const to = name.trim() || (kind === "iso" ? isoName(file.name) : "");
        if ((to || kind === "iso") && !NAME.test(to)) return setProblem("Names start with a letter or digit: letters, digits, . _ -");
        onClose();
        const dest = { project, pool: chosen.name, name: to, target: local && many ? target : "" };
        void run(`${kind === "iso" ? "Upload" : "Import"} ${file.name} into ${chosen.name}`, async (progress) => {
          const st = await k.send(file, dest, (f) =>
            progress(f < 1 ? `${Math.floor(f * 100)}% uploaded` : "uploaded; LXD is unpacking it"),
          );
          progress(k.done);
          return st;
        });
      }}
    >
      <p className="muted">
        {kind === "iso" ? (
          <>An installer or other ISO image, stored as a custom volume to attach to VMs as a CD.</>
        ) : (
          <>
            A tarball made by a volume backup here or by <code>lxc storage volume export</code>.
          </>
        )}{" "}
        It streams to LXD, so keep this tab open until the upload finishes.
      </p>
      <label className="field">
        {k.file}
        <input type="file" accept={k.accept} onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
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
        {kind === "iso" ? "Name (optional; from the file name if empty)" : "Name (optional; the backup's own if empty)"}
        <input value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      {problem && <p role="alert">{problem}</p>}
    </Confirm>
  );
}
