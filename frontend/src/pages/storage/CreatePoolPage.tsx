import { useState } from "react";
import { useNavigate } from "react-router";
import { stringify } from "yaml";
import { createPool, fetchMembers } from "../../api";
import { useActions } from "../../actions";
import { useSession } from "../../session";
import { useApi } from "../../useApi";
import YamlEditor from "../../YamlEditor";
import { asConfig } from "../instance/state";
import { poolPath } from "./paths";
import { buildPool, DRIVERS, emptyPool, FIELDS, LOCAL, poolProblems, SHARED, type PoolForm } from "./request";

const SOURCE_HINT: Record<string, string> = {
  dir: "Directory (blank: inside LXD's own directory)",
  zfs: "Disk, partition or existing ZFS pool (blank: loop file)",
  btrfs: "Disk, partition or existing path (blank: loop file)",
  lvm: "Disk or existing volume group (blank: loop file)",
};

export default function CreatePoolPage() {
  const { user } = useSession();
  const { run } = useActions();
  const navigate = useNavigate();
  const [f, setF] = useState<PoolForm>(emptyPool);
  const [tried, setTried] = useState(false);
  const [extraProblem, setExtraProblem] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const members = useApi("members", fetchMembers);
  const names = (members.data ?? []).map((m) => m.name);

  if (user.role !== "admin") return <p role="alert">Creating storage pools needs the admin role.</p>;

  const set = <K extends keyof PoolForm>(k: K, v: PoolForm[K]) => setF((x) => ({ ...x, [k]: v }));
  const setOwn = (m: string, patch: Partial<{ source: string; size: string }>) =>
    set("perMember", { ...f.perMember, [m]: { ...(f.perMember[m] ?? { source: "", size: "" }), ...patch } });
  const issues = [...poolProblems(f), ...(extraProblem ? [extraProblem] : [])];
  const req = buildPool(f, names);
  const local = LOCAL.has(f.driver);
  const first = names.length ? f.perMember[names[0]] : undefined;

  return (
    <form
      className="create"
      aria-label="Create storage pool"
      onSubmit={(e) => {
        e.preventDefault();
        setTried(true);
        if (issues.length) return;
        setError(null);
        void run(`Create pool ${req.name}`, () => createPool(req), { onError: setError }).then((ok) => ok && navigate(poolPath(req.name)));
      }}
    >
      <h1>Create storage pool</h1>
      <section aria-label="Basics">
        <label className="field">
          Name
          <input value={f.name} onChange={(e) => set("name", e.target.value)} />
        </label>
        <fieldset aria-label="Driver">
          {DRIVERS.map((d) => (
            <label key={d.driver}>
              <input type="radio" checked={f.driver === d.driver} onChange={() => set("driver", d.driver)} /> {d.label}{" "}
              <span className="muted">— {d.note}</span>
            </label>
          ))}
        </fieldset>
        <label className="field">
          Description
          <input value={f.description} onChange={(e) => set("description", e.target.value)} />
        </label>
      </section>

      {local && (
        <section aria-label="Per member">
          <h2>On each member</h2>
          {names.map((m) => (
            <div key={m} className="form-row">
              <label className="field">
                {m}: source
                <input placeholder={SOURCE_HINT[f.driver]} size={40} value={f.perMember[m]?.source ?? ""} onChange={(e) => setOwn(m, { source: e.target.value })} />
              </label>
              {f.driver !== "dir" && (
                <label className="field">
                  {m}: loop file size
                  <input placeholder="auto" size={8} value={f.perMember[m]?.size ?? ""} onChange={(e) => setOwn(m, { size: e.target.value })} />
                </label>
              )}
            </div>
          ))}
          {names.length > 1 && (
            <button
              type="button"
              disabled={!first || (!first.source.trim() && !first.size.trim())}
              onClick={() => set("perMember", Object.fromEntries(names.map((m) => [m, { ...first! }])))}
            >
              Use {names[0]}'s values on every member
            </button>
          )}
          <div className="form-row">
            {(SHARED[f.driver] ?? []).map((fd) => (
              <label key={fd.key} className="field">
                {fd.label}
                <input placeholder={fd.placeholder} value={f.shared[fd.key] ?? ""} onChange={(e) => set("shared", { ...f.shared, [fd.key]: e.target.value })} />
              </label>
            ))}
          </div>
        </section>
      )}

      <section aria-label="Settings">
        <h2>Settings</h2>
        <p className="muted">Leave blank for LXD's default.</p>
        <div className="form-row">
          {FIELDS[f.driver].map((fd) => (
            <label key={fd.key} className="field">
              {fd.label}
              <input placeholder={fd.placeholder} value={f.fields[fd.key] ?? ""} onChange={(e) => set("fields", { ...f.fields, [fd.key]: e.target.value })} />
            </label>
          ))}
        </div>
        <YamlEditor
          label="Other settings (YAML)"
          rows={4}
          value={f.extra}
          onChange={(v) => {
            const c = asConfig(v);
            setExtraProblem(typeof c === "string" ? c : null);
            if (typeof c !== "string") set("extra", c);
          }}
        />
      </section>

      <details>
        <summary>Request preview</summary>
        <pre className="log" aria-label="Request preview">
          {stringify(req)}
        </pre>
      </details>
      {tried && issues.length > 0 && (
        <ul role="alert">
          {issues.map((i) => (
            <li key={i}>{i}</li>
          ))}
        </ul>
      )}
      {error && <p role="alert">Creating the pool failed: {error}</p>}
      <div className="actions">
        <button type="submit" className="primary">
          Create
        </button>
        <button type="button" onClick={() => navigate("/storage")}>
          Cancel
        </button>
      </div>
    </form>
  );
}
