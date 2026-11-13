import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { createProfile, fetchProfiles } from "../../api";
import { useActions } from "../../actions";
import { useCurrentProject } from "../../project";
import { useApi } from "../../useApi";
import { NameDialog } from "../instance/Actions";
import { useAccess } from "../../access";

export default function ProfilesPage() {
  const access = useAccess();
  const { run, version } = useActions();
  const navigate = useNavigate();
  const project = useCurrentProject();
  const { data, error } = useApi(`profiles:${project}:${version}`, () => fetchProfiles(project));
  const [creating, setCreating] = useState(false);

  return (
    <>
      <h1>Profiles in {project}</h1>
      {access.can(project, "manage") && (
        <p>
          <button type="button" className="primary" onClick={() => setCreating(true)}>
            Create profile
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
                <th>Devices</th>
                <th className="num">Used by</th>
              </tr>
            </thead>
            <tbody>
              {data.map((p) => (
                <tr key={p.name}>
                  <td>
                    <Link to={`/profiles/${encodeURIComponent(project)}/${encodeURIComponent(p.name)}`}>{p.name}</Link>
                  </td>
                  <td>{p.description}</td>
                  <td>{Object.keys(p.devices).join(", ") || "–"}</td>
                  <td className="num">{p.used_by.length}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {creating && (
        <NameDialog
          title={`New profile in ${project}`}
          label="Profile name"
          onCancel={() => setCreating(false)}
          onSubmit={(name) => {
            setCreating(false);
            void run(`Create profile ${name}`, () => createProfile(project, name, { description: "", config: {}, devices: {} })).then(
              (ok) => ok && navigate(`/profiles/${encodeURIComponent(project)}/${encodeURIComponent(name)}`),
            );
          }}
        />
      )}
    </>
  );
}
