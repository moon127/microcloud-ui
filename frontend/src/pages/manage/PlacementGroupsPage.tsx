import { useState } from "react";
import { Link } from "react-router";
import {
  createPlacementGroup,
  deletePlacementGroup,
  fetchPlacementGroups,
  fetchServer,
  updatePlacementGroup,
  type PlacementGroup,
  type PlacementGroupBody,
} from "../../api";
import { useActions } from "../../actions";
import Confirm from "../../Confirm";
import { instancePath } from "../../paths";
import { useProject } from "../../project";
import { useSession } from "../../session";
import { useApi } from "../../useApi";

const POLICIES = {
  spread: "spread: one instance per member where possible",
  compact: "compact: keep instances on the same member",
} as const;
const RIGORS = {
  strict: "strict: refuse when LXD can't place it within the policy",
  permissive: "permissive: place anyway when the policy can't be met",
} as const;

type Editing = { name: string; body: PlacementGroupBody; isNew: boolean };

/** Placement groups (LXD 6.6+, Phase 5b). Instances join one through ``placement.group``. */
export default function PlacementGroupsPage() {
  const { user } = useSession();
  const { run, version } = useActions();
  const project = useProject().project || "default";
  const server = useApi("server", fetchServer);
  const supported = !!server.data?.features.placement_groups;
  const { data, error } = useApi(`placement-groups:${project}:${version}:${supported}`, () =>
    supported ? fetchPlacementGroups(project) : Promise.resolve([]),
  );
  const [editing, setEditing] = useState<Editing | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const admin = user.role === "admin";

  if (server.data && !supported) {
    return (
      <>
        <h1>Placement groups</h1>
        <p>
          Placement groups need LXD 6.6 or later (MicroCloud 3.x); this cluster runs LXD {server.data.version}. Until
          then, use a member or a cluster group as the target when creating an instance.
        </p>
      </>
    );
  }
  const edit = (g: PlacementGroup) =>
    setEditing({
      name: g.name,
      isNew: false,
      body: { description: g.description, policy: g.config.policy ?? "spread", rigor: g.config.rigor ?? "strict" },
    });

  return (
    <>
      <h1>Placement groups in {project}</h1>
      <p className="muted">
        An instance joins a group through its <code>placement.group</code> key (or when it&apos;s created). The group decides
        whether its instances are spread over members or kept together. LXD applies it when it picks the member, as
        when an instance is created without one; moving an instance to a named member isn&apos;t checked.
      </p>
      {admin && (
        <p>
          <button
            type="button"
            className="primary"
            onClick={() => setEditing({ name: "", isNew: true, body: { description: "", policy: "spread", rigor: "strict" } })}
          >
            Create placement group
          </button>
        </p>
      )}
      {error && <p role="alert">{error}</p>}
      {!data && !error && <p role="status">Loading…</p>}
      {data && data.length === 0 && <p>No placement groups in {project}.</p>}
      {data && data.length > 0 && (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Policy</th>
                <th>Rigor</th>
                <th>Description</th>
                <th>Instances</th>
                {admin && <th />}
              </tr>
            </thead>
            <tbody>
              {data.map((g) => (
                <tr key={g.name}>
                  <td>{g.name}</td>
                  <td>{g.config.policy ?? "–"}</td>
                  <td>{g.config.rigor ?? "–"}</td>
                  <td className="wrap">{g.description}</td>
                  <td className="wrap">
                    {g.used_by.length === 0
                      ? "none"
                      : g.used_by.map((u, i) => {
                          const name = decodeURIComponent(u.split("?")[0].split("/").pop() ?? "");
                          return (
                            <span key={u}>
                              {i > 0 && ", "}
                              <Link to={instancePath(project, name)}>{name}</Link>
                            </span>
                          );
                        })}
                  </td>
                  {admin && (
                    <td>
                      <button type="button" onClick={() => edit(g)}>
                        Edit
                      </button>{" "}
                      <button type="button" className="danger" onClick={() => setDeleting(g.name)}>
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
      {editing && (
        <Confirm
          title={editing.isNew ? `New placement group in ${project}` : `Edit ${editing.name}`}
          confirmLabel={editing.isNew ? "Create" : "Save"}
          onCancel={() => setEditing(null)}
          onConfirm={() => {
            const { name, body, isNew } = editing;
            if (!name.trim()) return;
            setEditing(null);
            void run(isNew ? `Create placement group ${name}` : `Save placement group ${name}`, () =>
              isNew ? createPlacementGroup(project, name.trim(), body) : updatePlacementGroup(project, name, body),
            );
          }}
        >
          <fieldset>
            {editing.isNew && (
              <label>
                Name <input value={editing.name} onChange={(e) => setEditing({ ...editing, name: e.target.value })} />
              </label>
            )}
            <label>
              Policy{" "}
              <select
                value={editing.body.policy}
                onChange={(e) => setEditing({ ...editing, body: { ...editing.body, policy: e.target.value as PlacementGroupBody["policy"] } })}
              >
                {Object.entries(POLICIES).map(([v, label]) => (
                  <option key={v} value={v}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Rigor{" "}
              <select
                value={editing.body.rigor}
                onChange={(e) => setEditing({ ...editing, body: { ...editing.body, rigor: e.target.value as PlacementGroupBody["rigor"] } })}
              >
                {Object.entries(RIGORS).map(([v, label]) => (
                  <option key={v} value={v}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Description{" "}
              <input
                value={editing.body.description}
                onChange={(e) => setEditing({ ...editing, body: { ...editing.body, description: e.target.value } })}
              />
            </label>
          </fieldset>
        </Confirm>
      )}
      {deleting && (
        <Confirm
          title={`Delete placement group ${deleting}?`}
          confirmLabel="Delete"
          danger
          onCancel={() => setDeleting(null)}
          onConfirm={() => {
            const name = deleting;
            setDeleting(null);
            void run(`Delete placement group ${name}`, () => deletePlacementGroup(project, name));
          }}
        >
          <p>LXD refuses while instances still use it; remove their <code>placement.group</code> first.</p>
        </Confirm>
      )}
    </>
  );
}
