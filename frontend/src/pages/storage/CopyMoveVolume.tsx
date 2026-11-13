import { useState } from "react";
import { useNavigate } from "react-router";
import { copyVolume, fetchMembers, fetchProjects, fetchStoragePools, moveVolume, type PoolDriver, type VolumeDetail } from "../../api";
import { useAccess } from "../../access";
import { useActions } from "../../actions";
import Confirm from "../../Confirm";
import { useApi } from "../../useApi";
import { volumePath } from "./paths";
import { LOCAL } from "./request";

const NAME = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;

/** Copy or move a custom volume to another pool or project (ST-4, Phase 10a); a copy to a
 * local pool can go to a chosen member. Manage in both projects (D84). */
export default function CopyMoveVolume({
  kind,
  project,
  pool,
  v,
  target,
  onClose,
}: {
  kind: "copy" | "move";
  project: string;
  pool: string;
  v: VolumeDetail;
  /** the volume's member, on a local pool */
  target?: string;
  onClose: () => void;
}) {
  const access = useAccess();
  const { run } = useActions();
  const navigate = useNavigate();
  const pools = useApi("pools", fetchStoragePools);
  const members = useApi("members", fetchMembers);
  const projects = useApi("projects", fetchProjects);
  const copy = kind === "copy";
  const [name, setName] = useState(copy ? `${v.name}-copy` : v.name);
  const [toPool, setToPool] = useState(pool);
  const [toProject, setToProject] = useState(project);
  const [member, setMember] = useState("");
  const [volumeOnly, setVolumeOnly] = useState(false);
  const [problem, setProblem] = useState("");
  const usable = (pools.data ?? []).filter((p) => p.driver !== "cephobject");
  const local = LOCAL.has(usable.find((p) => p.name === toPool)?.driver as PoolDriver);
  const many = (members.data ?? []).length > 1;
  const choices = (projects.data ?? [{ name: project }]).filter((p) => access.can(p.name, "manage"));
  const inUse = (v.used_by ?? []).length > 0;
  return (
    <Confirm
      title={`${copy ? "Copy" : "Move"} volume ${v.name}`}
      confirmLabel={copy ? "Copy" : "Move"}
      onCancel={onClose}
      onConfirm={() => {
        const to = name.trim();
        if (!NAME.test(to)) return setProblem("Names start with a letter or digit: letters, digits, . _ -");
        if (!copy && to === v.name && toPool === pool && toProject === project) return setProblem("Choose another pool, project or name.");
        onClose();
        const where = `${toProject}/${toPool}/${to}`;
        const dest = copy && local && many && member ? member : undefined;
        if (copy) {
          const body = { name: to, pool: toPool, volume_only: volumeOnly, ...(toProject !== project ? { project: toProject } : {}), ...(dest ? { target: dest } : {}) };
          void run(`Copy ${v.name} to ${where}`, () => copyVolume(project, pool, v.name, body, target));
        } else {
          const body = { ...(to !== v.name ? { name: to } : {}), ...(toPool !== pool ? { pool: toPool } : {}), ...(toProject !== project ? { project: toProject } : {}) };
          // a moved volume on a remote pool has no member; on a local one it stays put
          void run(`Move ${v.name} to ${where}`, () => moveVolume(project, pool, v.name, body, target)).then(
            (ok) => ok && navigate(volumePath(toProject, toPool, to, local ? target : undefined)),
          );
        }
      }}
    >
      {!copy && inUse && (
        <p className="muted">The instances using it keep it: LXD points their devices at its new place (LXD 6.10).</p>
      )}
      <label className="field">
        Name
        <input value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label className="field">
        Pool
        <select value={toPool} onChange={(e) => setToPool(e.target.value)}>
          {usable.map((p) => (
            <option key={p.name} value={p.name}>
              {p.name} ({p.driver})
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        Project
        <select value={toProject} onChange={(e) => setToProject(e.target.value)}>
          {choices.map((p) => (
            <option key={p.name}>{p.name}</option>
          ))}
        </select>
      </label>
      {copy && local && many && (
        <label className="field">
          Member
          <select value={member} onChange={(e) => setMember(e.target.value)}>
            <option value="">The one answering the request</option>
            {(members.data ?? []).map((m) => (
              <option key={m.name}>{m.name}</option>
            ))}
          </select>
        </label>
      )}
      {copy && (
        <label>
          <input type="checkbox" checked={volumeOnly} onChange={(e) => setVolumeOnly(e.target.checked)} /> Leave out its
          snapshots
        </label>
      )}
      {toProject !== project && (
        <p className="muted">LXD applies {toProject}&apos;s limits and restrictions to it.</p>
      )}
      {problem && <p role="alert">{problem}</p>}
    </Confirm>
  );
}
