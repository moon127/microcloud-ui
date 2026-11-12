import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { createVolume, fetchMembers, fetchStoragePools, fetchVolumes, volumeTarget, type NewVolume, type PoolDriver } from "../../api";
import { useActions } from "../../actions";
import Confirm from "../../Confirm";
import { useProject } from "../../project";
import { useSession } from "../../session";
import { useApi } from "../../useApi";
import { volumePath } from "./paths";
import { LOCAL } from "./request";

export default function VolumesPage() {
  const { user } = useSession();
  const { version } = useActions();
  const project = useProject().project || "default";
  const { data, error } = useApi(`volumes:${project}:${version}`, () => fetchVolumes(project), 60_000);
  const [pool, setPool] = useState("");
  const [creating, setCreating] = useState(false);
  const pools = [...new Set((data ?? []).map((v) => v.pool))];
  const shown = (data ?? []).filter((v) => !pool || v.pool === pool);
  return (
    <>
      <h1>Custom volumes in {project}</h1>
      <p className="actions">
        {user.role === "admin" && (
          <button type="button" className="primary" onClick={() => setCreating(true)}>
            Create volume
          </button>
        )}
        <Link to="/storage">Storage pools</Link>
      </p>
      {pools.length > 1 && (
        <label className="field">
          Pool
          <select value={pool} onChange={(e) => setPool(e.target.value)}>
            <option value="">All</option>
            {pools.map((p) => (
              <option key={p}>{p}</option>
            ))}
          </select>
        </label>
      )}
      {error && <p role="alert">{error}</p>}
      {!data && !error && <p role="status">Loading…</p>}
      {data && data.length === 0 && <p>No custom volumes.</p>}
      {shown.length > 0 && (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Pool</th>
                <th>Content</th>
                <th>Size</th>
                <th>Member</th>
                <th className="num">Used by</th>
                <th>Description</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((v) => (
                <tr key={`${v.pool}/${v.name}/${v.location}`}>
                  <td>
                    <Link to={volumePath(project, v.pool, v.name, volumeTarget(v))}>{v.name}</Link>
                  </td>
                  <td>{v.pool}</td>
                  <td>{v.content_type}</td>
                  <td>{v.config.size || "–"}</td>
                  <td>{volumeTarget(v) ?? "–"}</td>
                  <td className="num">{(v.used_by ?? []).length}</td>
                  <td>{v.description}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {creating && <CreateVolumeDialog project={project} onClose={() => setCreating(false)} />}
    </>
  );
}

function CreateVolumeDialog({ project, onClose }: { project: string; onClose: () => void }) {
  const { run } = useActions();
  const navigate = useNavigate();
  const pools = useApi("pools", fetchStoragePools);
  const members = useApi("members", fetchMembers);
  const [name, setName] = useState("");
  const [pool, setPool] = useState("");
  const [content, setContent] = useState<"filesystem" | "block">("filesystem");
  const [size, setSize] = useState("");
  const [target, setTarget] = useState("");
  const [description, setDescription] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const usable = (pools.data ?? []).filter((p) => p.driver !== "cephobject");
  const chosen = usable.find((p) => p.name === (pool || usable[0]?.name));
  const local = !!chosen && LOCAL.has(chosen.driver as PoolDriver);
  const cephfs = chosen?.driver === "cephfs";
  const many = (members.data ?? []).length > 1;
  return (
    <Confirm
      title={`New volume in ${project}`}
      confirmLabel="Create"
      onCancel={onClose}
      onConfirm={() => {
        if (!/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(name.trim())) return setProblem("Name: letters, digits, dots, dashes and underscores");
        if (!chosen) return setProblem("Choose a pool");
        onClose();
        const body: NewVolume = {
          name: name.trim(),
          pool: chosen.name,
          project,
          content_type: cephfs ? ("filesystem" as const) : content,
          description: description.trim(),
          config: size.trim() ? { size: size.trim() } : ({} as Record<string, string>),
          ...(local && many && target ? { target } : {}),
        };
        void run(`Create volume ${body.name}`, () => createVolume(body)).then(
          (ok) => ok && navigate(volumePath(project, body.pool, body.name, body.target)),
        );
      }}
    >
      <label className="field">
        Name
        <input value={name} onChange={(e) => setName(e.target.value)} />
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
      {!cephfs && (
        <fieldset className="inline" aria-label="Content type">
          <label>
            <input type="radio" checked={content === "filesystem"} onChange={() => setContent("filesystem")} /> Filesystem
          </label>
          <label>
            <input type="radio" checked={content === "block"} onChange={() => setContent("block")} /> Block (VMs)
          </label>
        </fieldset>
      )}
      <label className="field">
        Size
        <input placeholder="pool default" value={size} onChange={(e) => setSize(e.target.value)} />
      </label>
      <label className="field">
        Description
        <input value={description} onChange={(e) => setDescription(e.target.value)} />
      </label>
      {problem && <p role="alert">{problem}</p>}
    </Confirm>
  );
}
