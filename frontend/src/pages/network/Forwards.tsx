import { useState } from "react";
import { createForward, deleteForward, fetchForwards, updateForward, type ForwardPort, type NetworkForward } from "../../api";
import { useActions } from "../../actions";
import Confirm from "../../Confirm";
import { useApi } from "../../useApi";

const emptyPort = (): ForwardPort => ({ protocol: "tcp", listen_port: "", target_address: "", target_port: "", description: "" });

const portText = (p: ForwardPort) =>
  `${p.protocol} ${p.listen_port} → ${p.target_address}${p.target_port ? `:${p.target_port}` : ""}`;

/** A network's forwards (NW-5): listen address -> default target and per-port targets. */
export default function Forwards({
  project,
  network,
  admin,
  members,
}: {
  project: string;
  network: string;
  admin: boolean;
  members: string[];
}) {
  const { run, version } = useActions();
  const { data, error } = useApi(`forwards:${project}/${network}:${version}`, () => fetchForwards(project, network));
  const [editing, setEditing] = useState<NetworkForward | "new" | null>(null);
  const [deleting, setDeleting] = useState<NetworkForward | null>(null);
  return (
    <section aria-label="Forwards">
      <h2>Forwards</h2>
      {error && <p className="muted">Forwards unavailable: {error}</p>}
      {data && data.length === 0 && <p>None.</p>}
      {data && data.length > 0 && (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>Listen address</th>
                <th>Default target</th>
                <th>Ports</th>
                <th>Description</th>
                <th>Member</th>
                {admin && <th />}
              </tr>
            </thead>
            <tbody>
              {data.map((f) => (
                <tr key={`${f.listen_address}-${f.location ?? ""}`}>
                  <td>{f.listen_address}</td>
                  <td>{f.config?.target_address || "–"}</td>
                  <td className="wrap">{(f.ports ?? []).map(portText).join("; ") || "–"}</td>
                  <td>{f.description}</td>
                  <td>{f.location || "–"}</td>
                  {admin && (
                    <td>
                      <button type="button" onClick={() => setEditing(f)}>
                        Edit
                      </button>{" "}
                      <button type="button" className="danger" onClick={() => setDeleting(f)}>
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
      {admin && (
        <button type="button" onClick={() => setEditing("new")}>
          Add forward
        </button>
      )}
      {editing && (
        <ForwardDialog
          initial={editing === "new" ? null : editing}
          members={members}
          onCancel={() => setEditing(null)}
          onSave={(listen, body, target) => {
            setEditing(null);
            void run(`${editing === "new" ? "Add" : "Save"} forward ${listen}`, () =>
              editing === "new"
                ? createForward(project, network, { listen_address: listen, ...body }, target)
                : updateForward(project, network, listen, body, target),
            );
          }}
        />
      )}
      {deleting && (
        <Confirm
          title={`Delete forward ${deleting.listen_address}?`}
          confirmLabel="Delete"
          danger
          onCancel={() => setDeleting(null)}
          onConfirm={() => {
            const f = deleting;
            setDeleting(null);
            void run(`Delete forward ${f.listen_address}`, () => deleteForward(project, network, f.listen_address, f.location || undefined));
          }}
        />
      )}
    </section>
  );
}

function ForwardDialog({
  initial,
  members,
  onCancel,
  onSave,
}: {
  initial: NetworkForward | null;
  members: string[];
  onCancel: () => void;
  onSave: (listen: string, body: { description: string; config: Record<string, string>; ports: ForwardPort[] }, target?: string) => void;
}) {
  const [listen, setListen] = useState(initial?.listen_address ?? "");
  const [target, setTarget] = useState(initial?.location ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [dflt, setDflt] = useState(initial?.config?.target_address ?? "");
  const [ports, setPorts] = useState<ForwardPort[]>(() => (initial?.ports ?? []).map((p) => ({ ...emptyPort(), ...p })));
  const [tried, setTried] = useState(false);
  const setPort = (i: number, patch: Partial<ForwardPort>) => setPorts(ports.map((p, j) => (j === i ? { ...p, ...patch } : p)));
  const problem = !listen.trim()
    ? "Enter the listen address"
    : ports.some((p) => !p.listen_port.trim() || !p.target_address.trim())
      ? "Every port needs listen ports and a target address"
      : null;
  return (
    <Confirm
      title={initial ? `Edit forward ${initial.listen_address}` : "Add forward"}
      confirmLabel="Save"
      onCancel={onCancel}
      onConfirm={() => {
        setTried(true);
        if (problem) return;
        const config = { ...(initial?.config ?? {}) };
        if (dflt.trim()) config.target_address = dflt.trim();
        else delete config.target_address;
        onSave(
          listen.trim(),
          { description: description.trim(), config, ports: ports.map((p) => ({ ...p, listen_port: p.listen_port.trim(), target_address: p.target_address.trim(), target_port: p.target_port.trim() })) },
          target || undefined,
        );
      }}
    >
      <label className="field">
        Listen address
        <input value={listen} readOnly={!!initial} onChange={(e) => setListen(e.target.value)} />
      </label>
      {!initial && members.length > 1 && (
        <label className="field">
          Member (bridge networks in a cluster)
          <select value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="">None</option>
            {members.map((m) => (
              <option key={m}>{m}</option>
            ))}
          </select>
        </label>
      )}
      <label className="field">
        Default target address (optional)
        <input value={dflt} onChange={(e) => setDflt(e.target.value)} />
      </label>
      <label className="field">
        Description
        <input value={description} onChange={(e) => setDescription(e.target.value)} />
      </label>
      {ports.map((p, i) => (
        <fieldset key={i} className="inline" aria-label={`Port ${i + 1}`}>
          <label className="field">
            Protocol
            <select value={p.protocol} onChange={(e) => setPort(i, { protocol: e.target.value as ForwardPort["protocol"] })}>
              <option>tcp</option>
              <option>udp</option>
            </select>
          </label>
          <label className="field">
            Listen ports
            <input size={8} placeholder="80,443" value={p.listen_port} onChange={(e) => setPort(i, { listen_port: e.target.value })} />
          </label>
          <label className="field">
            Target address
            <input size={12} value={p.target_address} onChange={(e) => setPort(i, { target_address: e.target.value })} />
          </label>
          <label className="field">
            Target ports
            <input size={8} placeholder="same" value={p.target_port} onChange={(e) => setPort(i, { target_port: e.target.value })} />
          </label>
          <button type="button" aria-label={`Remove port ${i + 1}`} onClick={() => setPorts(ports.filter((_, j) => j !== i))}>
            ×
          </button>
        </fieldset>
      ))}
      <button type="button" onClick={() => setPorts([...ports, emptyPort()])}>
        Add port
      </button>
      {tried && problem && <p role="alert">{problem}</p>}
    </Confirm>
  );
}
