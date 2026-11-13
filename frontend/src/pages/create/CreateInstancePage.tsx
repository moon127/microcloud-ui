import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { stringify } from "yaml";
import {
  createInstance,
  fetchMembers,
  fetchNetworks,
  fetchPlacementGroups,
  fetchProfiles,
  fetchProjects,
  fetchServer,
  fetchStoragePools,
  operationProgress,
  type Operation,
} from "../../api";
import { useActions } from "../../actions";
import { instancePath } from "../../paths";
import { useCurrentProject } from "../../project";
import { useApi } from "../../useApi";
import ImagePicker from "./ImagePicker";
import { buildRequest, emptyForm, problems, type CreateForm, type ExtraDisk, type Nic } from "./request";
import { manageable, useAccess } from "../../access";

export default function CreateInstancePage() {
  const access = useAccess();
  const current = useCurrentProject();
  const { run } = useActions();
  const navigate = useNavigate();
  // a project user creates where they're a project admin
  const [f, setF] = useState<CreateForm>(() =>
    emptyForm(access.can(current, "manage") ? current : (manageable(access.user)[0] ?? current)),
  );
  const [tried, setTried] = useState(false);
  // the running create: its latest operation, or why it failed
  const [creating, setCreating] = useState<Progress | null>(null);
  const projects = useApi("projects", fetchProjects);
  const members = useApi("members", fetchMembers);
  const pools = useApi("pools", fetchStoragePools);
  const profiles = useApi(`profiles:${f.project}`, () => fetchProfiles(f.project));
  const server = useApi("server", fetchServer);
  const features = server.data?.features ?? {};
  const groups = useApi(`placement-groups:${f.project}:${features.placement_groups ?? false}`, () =>
    features.placement_groups ? fetchPlacementGroups(f.project) : Promise.resolve([]),
  );
  const networks = useApi(`networks:${f.project}`, () => fetchNetworks(f.project));

  if (!access.canManageSomewhere()) return <p role="alert">Creating instances needs the admin role in a project.</p>;

  const set = <K extends keyof CreateForm>(k: K, v: CreateForm[K]) => setF((x) => ({ ...x, [k]: v }));
  const busy = !!creating && !creating.error;
  const issues = problems(f);
  const req = buildRequest(f, features);
  const managed = (networks.data ?? []).filter((n) => n.managed);

  const setNic = (i: number, patch: Partial<Nic>) => set("nics", f.nics.map((n, j) => (j === i ? { ...n, ...patch } : n)));
  const setDisk = (i: number, patch: Partial<ExtraDisk>) =>
    set("disks", f.disks.map((d, j) => (j === i ? { ...d, ...patch } : d)));

  return (
    <form
      className="create"
      aria-label="Create instance"
      onSubmit={(e) => {
        e.preventDefault();
        setTried(true);
        if (issues.length) return;
        const { name, project, start } = f;
        setCreating({ name, start });
        void run(`Create ${name}`, () => createInstance(req), {
          onProgress: (op) => setCreating({ name, start, op }),
          onError: (error) => setCreating({ name, start, error }),
        }).then((ok) => ok && navigate(instancePath(project, name)));
      }}
    >
      <h1>Create instance</h1>

      <section aria-label="Basics">
        <div className="form-row">
          <label className="field">
            Name
            <input value={f.name} onChange={(e) => set("name", e.target.value)} />
          </label>
          <label className="field">
            Project
            <select value={f.project} onChange={(e) => set("project", e.target.value)}>
              {(projects.data ?? [{ name: f.project }]).filter((p) => access.can(p.name, "manage")).map((p) => (
                <option key={p.name}>{p.name}</option>
              ))}
            </select>
          </label>
          <fieldset className="inline" aria-label="Type">
            <label>
              <input type="radio" checked={f.type === "container"} onChange={() => set("type", "container")} /> Container
            </label>
            <label>
              <input type="radio" checked={f.type === "virtual-machine"} onChange={() => set("type", "virtual-machine")} /> VM
            </label>
          </fieldset>
        </div>
        <label className="field">
          Description
          <input value={f.description} onChange={(e) => set("description", e.target.value)} />
        </label>
      </section>

      <section aria-label="Image">
        <h2>Image</h2>
        <ImagePicker project={f.project} type={f.type} value={f.image} onChange={(img) => set("image", img)} />
      </section>

      <section aria-label="Placement and profiles">
        <h2>Placement and profiles</h2>
        <div className="form-row">
          <label className="field">
            Member
            <select value={f.target} onChange={(e) => set("target", e.target.value)}>
              <option value="">Let LXD choose</option>
              {(members.data ?? []).map((m) => (
                <option key={m.name} value={m.name}>
                  {m.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            Or cluster group
            <input
              placeholder="@group"
              value={f.target.startsWith("@") ? f.target : ""}
              onChange={(e) => set("target", e.target.value ? `@${e.target.value.replace(/^@/, "")}` : "")}
            />
          </label>
          {features.placement_groups && (
            <label className="field">
              Placement group
              <select value={f.placementGroup} onChange={(e) => set("placementGroup", e.target.value)}>
                <option value="">None</option>
                {(groups.data ?? []).map((g) => (
                  <option key={g.name} value={g.name}>
                    {g.name} ({g.config.policy}
                    {g.config.rigor === "permissive" ? ", permissive" : ""})
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
        <fieldset aria-label="Profiles">
          {(profiles.data ?? []).map((p) => (
            <label key={p.name}>
              <input
                type="checkbox"
                checked={f.profiles.includes(p.name)}
                onChange={(e) =>
                  set("profiles", e.target.checked ? [...f.profiles, p.name] : f.profiles.filter((x) => x !== p.name))
                }
              />{" "}
              {p.name} {p.description && <span className="muted">— {p.description}</span>}
            </label>
          ))}
        </fieldset>
      </section>

      <section aria-label="Resources">
        <h2>Resources</h2>
        <div className="form-row">
          <label className="field">
            CPU limit
            <input placeholder="from profile" value={f.cpu} onChange={(e) => set("cpu", e.target.value)} />
          </label>
          <label className="field">
            Memory limit
            <input placeholder="e.g. 1GiB" value={f.memory} onChange={(e) => set("memory", e.target.value)} />
          </label>
          <label className="field">
            Root disk pool
            <select value={f.rootPool} onChange={(e) => set("rootPool", e.target.value)}>
              <option value="">From profile</option>
              {(pools.data ?? []).map((p) => (
                <option key={p.name} value={p.name}>
                  {p.name} ({p.driver})
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            Root disk size
            <input placeholder="e.g. 10GiB" value={f.rootSize} onChange={(e) => set("rootSize", e.target.value)} />
          </label>
        </div>
      </section>

      <section aria-label="Network">
        <h2>Network</h2>
        <p className="muted">Without NICs here, the profiles' NICs are used.</p>
        {f.nics.map((n, i) => (
          <div className="form-row" key={i}>
            <label className="field">
              Device
              <input value={n.name} onChange={(e) => setNic(i, { name: e.target.value })} />
            </label>
            <label className="field">
              Network
              <select value={n.network} onChange={(e) => setNic(i, { network: e.target.value })}>
                <option value="">Choose…</option>
                {managed.map((m) => (
                  <option key={m.name} value={m.name}>
                    {m.name} ({m.type})
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              Static IPv4 (optional)
              <input value={n.ipv4} onChange={(e) => setNic(i, { ipv4: e.target.value })} />
            </label>
            <button type="button" onClick={() => set("nics", f.nics.filter((_, j) => j !== i))}>
              Remove
            </button>
          </div>
        ))}
        <button type="button" onClick={() => set("nics", [...f.nics, { name: `eth${f.nics.length}`, network: "", ipv4: "" }])}>
          Add NIC
        </button>
      </section>

      <section aria-label="Extra disks">
        <h2>Extra disks</h2>
        {f.disks.map((d, i) => (
          <div className="form-row" key={i}>
            <label className="field">
              Device
              <input value={d.name} onChange={(e) => setDisk(i, { name: e.target.value })} />
            </label>
            <label className="field">
              Pool
              <select value={d.pool} onChange={(e) => setDisk(i, { pool: e.target.value })}>
                <option value="">Choose…</option>
                {(pools.data ?? []).map((p) => (
                  <option key={p.name}>{p.name}</option>
                ))}
              </select>
            </label>
            <label className="field">
              Custom volume
              <input value={d.volume} onChange={(e) => setDisk(i, { volume: e.target.value })} />
            </label>
            <label className="field">
              Mount path
              <input value={d.path} onChange={(e) => setDisk(i, { path: e.target.value })} />
            </label>
            <button type="button" onClick={() => set("disks", f.disks.filter((_, j) => j !== i))}>
              Remove
            </button>
          </div>
        ))}
        <button
          type="button"
          onClick={() => set("disks", [...f.disks, { name: `disk${f.disks.length + 1}`, pool: "", volume: "", path: "" }])}
        >
          Add disk
        </button>
      </section>

      <section aria-label="Cloud-init">
        <h2>Cloud-init</h2>
        <div className="grid">
          {(
            [
              ["userData", "User data"],
              ["vendorData", "Vendor data"],
              ["networkConfig", "Network config"],
            ] as const
          ).map(([k, label]) => (
            <label key={k} className="yaml">
              {label}
              <textarea rows={6} spellCheck={false} value={f[k]} onChange={(e) => set(k, e.target.value)} />
            </label>
          ))}
        </div>
      </section>

      <section aria-label="Security and boot">
        <h2>Security and boot</h2>
        {f.type === "container" ? (
          <>
            <label>
              <input type="checkbox" checked={f.nesting} onChange={(e) => set("nesting", e.target.checked)} /> Allow nesting
            </label>
            <label>
              <input type="checkbox" checked={f.privileged} onChange={(e) => set("privileged", e.target.checked)} /> Privileged
            </label>
          </>
        ) : (
          <label>
            <input type="checkbox" checked={f.secureboot} onChange={(e) => set("secureboot", e.target.checked)} /> Secure boot
          </label>
        )}
        <label>
          <input type="checkbox" checked={f.autostart} onChange={(e) => set("autostart", e.target.checked)} /> Start on host
          boot
        </label>
        <label>
          <input type="checkbox" checked={f.start} onChange={(e) => set("start", e.target.checked)} /> Start after creating
        </label>
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
      {creating && <CreateProgress {...creating} />}
      <div className="actions">
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Creating…" : "Create"}
        </button>
        <button type="button" onClick={() => navigate("/instances")}>
          Cancel
        </button>
      </div>
    </form>
  );
}

interface Progress {
  name: string;
  start: boolean;
  op?: Operation;
  error?: string;
}

function CreateProgress({ name, start, op, error }: Progress) {
  if (error)
    return (
      <p role="alert" className="progress failed">
        Creating {name} failed: {error}
      </p>
    );
  const note = op && operationProgress(op);
  return (
    <div role="status" className="progress">
      <strong>
        Creating {name}
        {start ? " and starting it" : ""}…
      </strong>{" "}
      {note ?? (op ? op.description : "Sending the request to LXD")}
      {op && (
        <>
          {" · "}
          <Link to="/operations">Operations</Link>
        </>
      )}
      <div className="muted">Downloading a new image can take a few minutes; you'll be taken to the instance when it's ready.</div>
    </div>
  );
}
