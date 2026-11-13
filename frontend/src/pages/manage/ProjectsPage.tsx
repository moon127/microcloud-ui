import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { createProject, fetchProjects } from "../../api";
import { useActions } from "../../actions";
import Confirm from "../../Confirm";
import { useApi } from "../../useApi";
import { useAccess } from "../../access";

const FEATURES = [
  { key: "features.images", label: "Own images", on: true },
  { key: "features.profiles", label: "Own profiles", on: true },
  { key: "features.storage.volumes", label: "Own storage volumes", on: true },
  { key: "features.storage.buckets", label: "Own storage buckets", on: true },
  { key: "features.networks", label: "Own networks", on: false },
  { key: "features.networks.zones", label: "Own network zones", on: false },
];

export default function ProjectsPage() {
  const access = useAccess();
  const { version } = useActions();
  const { data, error } = useApi(`projects:${version}`, fetchProjects);
  const [creating, setCreating] = useState(false);

  return (
    <>
      <h1>Projects</h1>
      {access.clusterAdmin && (
        <p>
          <button type="button" className="primary" onClick={() => setCreating(true)}>
            Create project
          </button>
        </p>
      )}
      {error && <p role="alert">{error}</p>}
      {!data && !error && <p role="status">Loading…</p>}
      {data && (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Description</th>
                <th>Own</th>
                <th>Restricted</th>
                <th>Limits</th>
                <th className="num">Used by</th>
              </tr>
            </thead>
            <tbody>
              {data.map((p) => (
                <tr key={p.name}>
                  <td>
                    <Link to={`/projects/${encodeURIComponent(p.name)}`}>{p.name}</Link>
                  </td>
                  <td className="wrap">{p.description}</td>
                  <td>
                    {FEATURES.filter((f) => p.config[f.key] === "true")
                      .map((f) => f.key.replace("features.", ""))
                      .join(", ") || "–"}
                  </td>
                  <td>{p.config.restricted === "true" ? "yes" : "–"}</td>
                  <td>
                    {Object.entries(p.config)
                      .filter(([k]) => k.startsWith("limits."))
                      .map(([k, v]) => `${k.slice(7)}=${v}`)
                      .join(", ") || "–"}
                  </td>
                  <td className="num">{p.used_by.length}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {creating && <CreateProject onClose={() => setCreating(false)} />}
    </>
  );
}

function CreateProject({ onClose }: { onClose: () => void }) {
  const { run } = useActions();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [features, setFeatures] = useState(() => Object.fromEntries(FEATURES.map((f) => [f.key, f.on])));
  const [restricted, setRestricted] = useState(false);
  // LXD's restricted default blocks both, which leaves operators little to do (D83)
  const [keep, setKeep] = useState(true);
  return (
    <Confirm
      title="Create project"
      confirmLabel="Create"
      onCancel={onClose}
      onConfirm={() => {
        const n = name.trim();
        if (!n) return;
        onClose();
        const config = {
          ...Object.fromEntries(Object.entries(features).map(([k, v]) => [k, String(v)])),
          ...(restricted ? { restricted: "true" } : {}),
          ...(restricted && keep ? { "restricted.snapshots": "allow", "restricted.backups": "allow" } : {}),
        };
        void run(`Create project ${n}`, () => createProject(n, { description, config })).then(
          (ok) => ok && navigate(`/projects/${encodeURIComponent(n)}`),
        );
      }}
    >
      <label className="field">
        Name
        <input value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label className="field">
        Description
        <input value={description} onChange={(e) => setDescription(e.target.value)} />
      </label>
      {FEATURES.map((f) => (
        <label key={f.key}>
          <input
            type="checkbox"
            checked={features[f.key]}
            onChange={(e) => setFeatures({ ...features, [f.key]: e.target.checked })}
          />{" "}
          {f.label}
        </label>
      ))}
      <p className="muted">Features can't be changed once the project has instances or images.</p>
      <label>
        <input type="checkbox" checked={restricted} onChange={(e) => setRestricted(e.target.checked)} /> Restricted
      </label>
      <p className="muted">
        LXD then refuses privileged instances, host disks and similar inside it. Project users can only be operators or
        admins of a restricted project (docs/USERS.md).
      </p>
      {restricted && (
        <label>
          <input type="checkbox" checked={keep} onChange={(e) => setKeep(e.target.checked)} /> Allow snapshots and backups
          (a restricted project blocks both unless allowed)
        </label>
      )}
    </Confirm>
  );
}
