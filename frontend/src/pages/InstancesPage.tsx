import { useMemo, useState } from "react";
import { Link } from "react-router";
import { changeState, deleteInstance, fetchInstances, type Instance, type Started, type StateAction } from "../api";
import { useActions } from "../actions";
import Confirm from "../Confirm";
import { InstanceLink, MemberLink } from "../links";
import { useProject } from "../project";
import { useSession } from "../session";
import { useApi } from "../useApi";

const key = (i: Instance) => `${i.project}/${i.name}`;
const uniq = (xs: string[]) => Array.from(new Set(xs.filter(Boolean))).sort();

const BULK: { action: StateAction; label: string }[] = [
  { action: "start", label: "Start" },
  { action: "stop", label: "Stop" },
  { action: "restart", label: "Restart" },
];

export default function InstancesPage() {
  const { user } = useSession();
  const { run, version } = useActions();
  const { project } = useProject();
  const { data, error } = useApi(`instances:${version}`, fetchInstances, 30_000);
  const [text, setText] = useState("");
  const [status, setStatus] = useState("");
  const [type, setType] = useState("");
  const [member, setMember] = useState("");
  const [profile, setProfile] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [confirmDelete, setConfirmDelete] = useState(false);
  const admin = user.role === "admin";

  const all = useMemo(() => (data ?? []).filter((i) => !project || i.project === project), [data, project]);
  const rows = useMemo(() => {
    const q = text.trim().toLowerCase();
    return all.filter(
      (i) =>
        (!q || `${i.name} ${i.description}`.toLowerCase().includes(q)) &&
        (!status || i.status === status) &&
        (!type || i.type === type) &&
        (!member || i.location === member) &&
        (!profile || (i.profiles ?? []).includes(profile)),
    );
  }, [all, text, status, type, member, profile]);
  const chosen = rows.filter((i) => selected.has(key(i)));

  const toggle = (k: string) =>
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(k)) n.delete(k);
      else n.add(k);
      return n;
    });
  const bulk = (label: string, fn: (i: Instance) => Promise<Started>) => {
    for (const i of chosen) void run(`${label} ${i.name}`, () => fn(i));
    setSelected(new Set());
  };

  const select = (label: string, value: string, set: (v: string) => void, options: string[]) => (
    <label className="field">
      {label}
      <select value={value} onChange={(e) => set(e.target.value)}>
        <option value="">Any</option>
        {options.map((o) => (
          <option key={o}>{o}</option>
        ))}
      </select>
    </label>
  );

  return (
    <>
      <h1>Instances{project ? ` in ${project}` : ""}</h1>
      {admin && (
        <p>
          <Link className="button primary" to="/instances/new">
            Create instance
          </Link>
        </p>
      )}
      {error && <p role="alert">{error}</p>}
      <div className="filters">
        <label className="field">
          Search
          <input
            type="search"
            placeholder="Name or description"
            aria-label="Filter instances"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
        </label>
        {select("Status", status, setStatus, uniq(all.map((i) => i.status)))}
        {select("Type", type, setType, uniq(all.map((i) => i.type)))}
        {select("Member", member, setMember, uniq(all.map((i) => i.location)))}
        {select("Profile", profile, setProfile, uniq(all.flatMap((i) => i.profiles ?? [])))}
      </div>
      {admin && (
        <div className="actions" role="toolbar" aria-label="Bulk actions">
          <span className="muted">{chosen.length} selected</span>
          {BULK.map((b) => (
            <button
              key={b.action}
              type="button"
              disabled={chosen.length === 0}
              onClick={() => bulk(b.label, (i) => changeState(i.project, i.name, b.action))}
            >
              {b.label}
            </button>
          ))}
          <button type="button" className="danger" disabled={chosen.length === 0} onClick={() => setConfirmDelete(true)}>
            Delete
          </button>
        </div>
      )}
      {!data && !error && <p role="status">Loading…</p>}
      {data && (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                {admin && (
                  <th>
                    <input
                      type="checkbox"
                      aria-label="Select all"
                      checked={rows.length > 0 && chosen.length === rows.length}
                      onChange={(e) => setSelected(new Set(e.target.checked ? rows.map(key) : []))}
                    />
                  </th>
                )}
                <th>Name</th>
                <th>Project</th>
                <th>Type</th>
                <th>Status</th>
                <th>Member</th>
                <th>Profiles</th>
                <th>Networks</th>
                <th>Description</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((i) => (
                <tr key={key(i)}>
                  {admin && (
                    <td>
                      <input
                        type="checkbox"
                        aria-label={`Select ${i.name}`}
                        checked={selected.has(key(i))}
                        onChange={() => toggle(key(i))}
                      />
                    </td>
                  )}
                  <td>
                    <InstanceLink project={i.project} name={i.name} />
                  </td>
                  <td>{i.project}</td>
                  <td>{i.type === "virtual-machine" ? "VM" : "container"}</td>
                  <td>{i.status}</td>
                  <td>{i.location ? <MemberLink name={i.location} /> : "–"}</td>
                  <td>{(i.profiles ?? []).join(", ")}</td>
                  <td>{Array.from(new Set(Object.values(i.nics))).join(", ") || "–"}</td>
                  <td>{i.description}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {rows.length === 0 && <p>No instances.</p>}
        </div>
      )}
      {confirmDelete && (
        <Confirm
          title={`Delete ${chosen.length} instance${chosen.length === 1 ? "" : "s"}?`}
          confirmLabel="Delete"
          danger
          onCancel={() => setConfirmDelete(false)}
          onConfirm={() => {
            setConfirmDelete(false);
            bulk("Delete", (i) => deleteInstance(i.project, i.name, i.status !== "Stopped"));
          }}
        >
          <p>{chosen.map((i) => i.name).join(", ")}</p>
          <p>Running instances are stopped first. Snapshots are deleted too. This can't be undone.</p>
        </Confirm>
      )}
    </>
  );
}
