import { useState } from "react";
import {
  createUser,
  deleteUser,
  fetchProjects,
  fetchUsers,
  removeGrant,
  setGrant,
  updateUser,
  type ProjectRole,
  type Role,
  type User,
} from "../../api";
import { useAccess } from "../../access";
import { useActions } from "../../actions";
import Confirm from "../../Confirm";
import { useApi } from "../../useApi";

const ROLE_LABELS: Record<Role, string> = {
  admin: "cluster admin",
  viewer: "cluster viewer",
  project: "project user",
};
const ROLE_HELP: Record<Role, string> = {
  admin: "everything, including users, projects and the cluster",
  viewer: "reads everything, changes nothing",
  project: "only the projects granted below, with a role in each",
};
const PROJECT_ROLES: { value: ProjectRole; label: string }[] = [
  { value: "viewer", label: "viewer: sees the project's instances and resources" },
  { value: "operator", label: "operator: also starts and stops, consoles, files, snapshots" },
  { value: "admin", label: "admin: also creates, changes and deletes in the project" },
];
// nothing returned that a toast needs: users and grants change at once, not via LXD
const done = () => undefined;

type Dialog =
  | { kind: "create" }
  | { kind: "edit"; username: string }
  | { kind: "grants"; username: string }
  | { kind: "delete"; username: string }
  | null;

/** Users and their project grants (cluster admins; D81). */
export default function UsersPage() {
  const access = useAccess();
  const { version } = useActions();
  const users = useApi(`users:${version}`, fetchUsers);
  const [dialog, setDialog] = useState<Dialog>(null);
  if (!access.clusterAdmin) return <p role="alert">Managing users needs the cluster admin role.</p>;
  const find = (name: string) => users.data?.find((u) => u.username === name);
  const close = () => setDialog(null);

  return (
    <>
      <h1>Users</h1>
      <p className="muted">
        A project user sees and changes only the LXD projects granted to them. Operator and admin grants need a restricted
        project (<code>restricted=true</code>), so LXD itself blocks privileged instances and host access there.
      </p>
      <p>
        <button type="button" className="primary" onClick={() => setDialog({ kind: "create" })}>
          Create user
        </button>
      </p>
      {users.error && <p role="alert">{users.error}</p>}
      {!users.data && !users.error && <p role="status">Loading…</p>}
      {users.data && (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>User</th>
                <th>Role</th>
                <th>Projects</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {users.data.map((u) => (
                <tr key={u.username}>
                  <td>{u.username}</td>
                  <td>{ROLE_LABELS[u.role]}</td>
                  <td>
                    {u.role !== "project"
                      ? "all"
                      : Object.entries(u.grants ?? {})
                          .map(([p, r]) => `${p}: ${r}`)
                          .join(", ") || "none yet"}
                  </td>
                  <td className="actions">
                    <button type="button" onClick={() => setDialog({ kind: "edit", username: u.username })}>
                      Edit
                    </button>
                    {u.role === "project" && (
                      <button type="button" onClick={() => setDialog({ kind: "grants", username: u.username })}>
                        Projects
                      </button>
                    )}
                    {u.username !== access.user.username && (
                      <button type="button" className="danger" onClick={() => setDialog({ kind: "delete", username: u.username })}>
                        Delete
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {dialog?.kind === "create" && <CreateUser onClose={close} />}
      {dialog?.kind === "edit" && find(dialog.username) && <EditUser user={find(dialog.username)!} onClose={close} />}
      {dialog?.kind === "grants" && find(dialog.username) && <Grants user={find(dialog.username)!} onClose={close} />}
      {dialog?.kind === "delete" && <DeleteUser username={dialog.username} onClose={close} />}
    </>
  );
}

function RoleSelect({ value, onChange }: { value: Role; onChange: (r: Role) => void }) {
  return (
    <label className="field">
      Role
      <select value={value} onChange={(e) => onChange(e.target.value as Role)}>
        {(Object.keys(ROLE_LABELS) as Role[]).map((r) => (
          <option key={r} value={r}>
            {ROLE_LABELS[r]}: {ROLE_HELP[r]}
          </option>
        ))}
      </select>
    </label>
  );
}

function CreateUser({ onClose }: { onClose: () => void }) {
  const { run } = useActions();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<Role>("project");
  const [problem, setProblem] = useState("");
  return (
    <Confirm
      title="Create user"
      confirmLabel="Create"
      onCancel={onClose}
      onConfirm={() => {
        const name = username.trim();
        if (!name || !password) return setProblem("Enter a username and a password.");
        onClose();
        void run(`Create user ${name}`, () => createUser(name, password, role).then(done));
      }}
    >
      <label className="field">
        Username
        <input value={username} autoComplete="off" onChange={(e) => setUsername(e.target.value)} />
      </label>
      <label className="field">
        Password
        <input type="password" value={password} autoComplete="new-password" onChange={(e) => setPassword(e.target.value)} />
      </label>
      <RoleSelect value={role} onChange={setRole} />
      {role === "project" && <p className="muted">Grant projects once the user exists.</p>}
      {problem && <p role="alert">{problem}</p>}
    </Confirm>
  );
}

function EditUser({ user, onClose }: { user: User; onClose: () => void }) {
  const { run } = useActions();
  const [role, setRole] = useState<Role>(user.role);
  const [password, setPassword] = useState("");
  return (
    <Confirm
      title={`Edit ${user.username}`}
      confirmLabel="Save"
      onCancel={onClose}
      onConfirm={() => {
        onClose();
        const change = { ...(role !== user.role ? { role } : {}), ...(password ? { password } : {}) };
        if (Object.keys(change).length) void run(`Update user ${user.username}`, () => updateUser(user.username, change).then(done));
      }}
    >
      <RoleSelect value={role} onChange={setRole} />
      {user.role === "project" && role !== "project" && (
        <p className="muted">With a global role their project grants no longer apply.</p>
      )}
      <label className="field">
        New password (leave empty to keep it; changing it signs them out)
        <input type="password" value={password} autoComplete="new-password" onChange={(e) => setPassword(e.target.value)} />
      </label>
    </Confirm>
  );
}

function Grants({ user, onClose }: { user: User; onClose: () => void }) {
  const { run } = useActions();
  const projects = useApi("projects", fetchProjects);
  const [project, setProject] = useState("");
  const [role, setRole] = useState<ProjectRole>("viewer");
  const grants = Object.entries(user.grants ?? {});
  const restricted = (name: string) => projects.data?.find((p) => p.name === name)?.config.restricted === "true";
  return (
    <Confirm title={`Projects for ${user.username}`} confirmLabel="Done" onCancel={onClose} onConfirm={onClose}>
      {grants.length === 0 ? (
        <p>No projects yet.</p>
      ) : (
        <table>
          <tbody>
            {grants.map(([p, r]) => (
              <tr key={p}>
                <td>{p}</td>
                <td>{r}</td>
                <td>
                  <button type="button" onClick={() => void run(`Remove ${user.username} from ${p}`, () => removeGrant(user.username, p).then(done))}>
                    Remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <h3>Add a project</h3>
      <label className="field">
        Project
        <select value={project} onChange={(e) => setProject(e.target.value)}>
          <option value="">Choose…</option>
          {(projects.data ?? []).map((p) => (
            <option key={p.name} value={p.name}>
              {p.name}
              {p.config.restricted === "true" ? " (restricted)" : ""}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        Role
        <select value={role} onChange={(e) => setRole(e.target.value as ProjectRole)}>
          {PROJECT_ROLES.map((r) => (
            <option key={r.value} value={r.value}>
              {r.label}
            </option>
          ))}
        </select>
      </label>
      {project && role !== "viewer" && !restricted(project) && (
        <p role="alert">
          {project} isn&apos;t restricted, so only a viewer grant is allowed there. Set <code>restricted=true</code> on the
          project first.
        </p>
      )}
      <p>
        <button
          type="button"
          disabled={!project}
          onClick={() => void run(`Give ${user.username} ${role} in ${project}`, () => setGrant(user.username, project, role).then(done))}
        >
          Grant
        </button>
      </p>
    </Confirm>
  );
}

function DeleteUser({ username, onClose }: { username: string; onClose: () => void }) {
  const { run } = useActions();
  return (
    <Confirm
      title={`Delete ${username}?`}
      confirmLabel="Delete"
      danger
      onCancel={onClose}
      onConfirm={() => {
        onClose();
        void run(`Delete user ${username}`, () => deleteUser(username).then(done));
      }}
    >
      <p>They're signed out and can't sign in again.</p>
    </Confirm>
  );
}
