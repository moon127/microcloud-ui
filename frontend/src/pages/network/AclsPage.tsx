import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { createAcl, fetchAcls } from "../../api";
import { useActions } from "../../actions";
import { useProject } from "../../project";
import { useSession } from "../../session";
import { useApi } from "../../useApi";
import { NameDialog } from "../instance/Actions";
import { aclPath } from "./paths";

export default function AclsPage() {
  const { user } = useSession();
  const { run, version } = useActions();
  const navigate = useNavigate();
  const project = useProject().project || "default";
  const { data, error } = useApi(`acls:${project}:${version}`, () => fetchAcls(project));
  const [creating, setCreating] = useState(false);
  return (
    <>
      <h1>Network ACLs in {project}</h1>
      <p className="muted">
        Attach an ACL to a network on the network's page, or to an OVN NIC with its <code>security.acls</code> key.{" "}
        <Link to="/networks">Networks</Link>
      </p>
      {user.role === "admin" && (
        <p>
          <button type="button" className="primary" onClick={() => setCreating(true)}>
            Create ACL
          </button>
        </p>
      )}
      {error && <p role="alert">{error}</p>}
      {!data && !error && <p role="status">Loading…</p>}
      {data && data.length === 0 && <p>No ACLs.</p>}
      {data && data.length > 0 && (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Description</th>
                <th className="num">Ingress rules</th>
                <th className="num">Egress rules</th>
                <th className="num">Used by</th>
              </tr>
            </thead>
            <tbody>
              {data.map((a) => (
                <tr key={a.name}>
                  <td>
                    <Link to={aclPath(project, a.name)}>{a.name}</Link>
                  </td>
                  <td>{a.description}</td>
                  <td className="num">{(a.ingress ?? []).length}</td>
                  <td className="num">{(a.egress ?? []).length}</td>
                  <td className="num">{(a.used_by ?? []).length}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {creating && (
        <NameDialog
          title={`New ACL in ${project}`}
          label="ACL name"
          onCancel={() => setCreating(false)}
          onSubmit={(name) => {
            setCreating(false);
            void run(`Create ACL ${name}`, () => createAcl(project, name, { description: "", ingress: [], egress: [], config: {} })).then(
              (ok) => ok && navigate(aclPath(project, name)),
            );
          }}
        />
      )}
    </>
  );
}
