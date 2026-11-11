import { useEffect, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router";
import {
  deleteNetwork,
  fetchNetwork,
  fetchNetworkAllocations,
  fetchNetworkLeases,
  fetchNetworkMembers,
  fetchNetworkState,
  updateNetwork,
  type NetworkMember,
  type NetworkState,
} from "../../api";
import { useActions } from "../../actions";
import Confirm from "../../Confirm";
import { formatBytes, formatNumber } from "../../format";
import { MemberLink, UsedBy } from "../../links";
import { useSession } from "../../session";
import { useApi } from "../../useApi";
import YamlEditor from "../../YamlEditor";
import { asConfig } from "../instance/state";

interface Draft {
  description: string;
  config: Record<string, string>;
}

export default function NetworkPage() {
  const { project = "default", name = "" } = useParams();
  const [params] = useSearchParams();
  const member = params.get("member") ?? undefined; // unmanaged interfaces belong to one member
  const { user } = useSession();
  const { run, version } = useActions();
  const navigate = useNavigate();
  const net = useApi(`network:${project}/${name}:${member}:${version}`, () => fetchNetwork(project, name, member));
  const n = net.data;
  const managed = !!n?.managed;
  const admin = user.role === "admin" && managed;
  const [draft, setDraft] = useState<Draft | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    if (n) setDraft({ description: n.description, config: n.config });
  }, [n]);

  return (
    <>
      <h1>
        Network {name}{" "}
        <span className="muted">
          {n ? `${n.type} · ` : ""}
          {member ? `on ${member}` : `in ${project}`}
        </span>
      </h1>
      {net.error && <p role="alert">{net.error}</p>}
      {n && draft && (
        <>
          <div className="hardware">
            <dl>
              <dt>Status</dt>
              <dd>{n.status || (managed ? "–" : "Not managed by LXD")}</dd>
              {(n.locations ?? []).length > 0 && (
                <>
                  <dt>Members</dt>
                  <dd>
                    {(n.locations ?? []).map((m, i) => (
                      <span key={m}>
                        {i > 0 && ", "}
                        <MemberLink name={m} />
                      </span>
                    ))}
                  </dd>
                </>
              )}
            </dl>
          </div>
          {admin && (
            <div className="actions" role="toolbar" aria-label="Network actions">
              <button
                type="button"
                className="primary"
                disabled={problem !== null}
                onClick={() => void run(`Save network ${name}`, () => updateNetwork(project, name, draft))}
              >
                Save
              </button>
              <button type="button" className="danger" onClick={() => setDeleting(true)}>
                Delete
              </button>
            </div>
          )}
          {managed && (
            <>
              <fieldset disabled={!admin}>
                <label className="field">
                  Description
                  <input value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} />
                </label>
              </fieldset>
              <YamlEditor
                label="Config (YAML), cluster-wide"
                rows={10}
                value={draft.config}
                readOnly={!admin}
                onChange={(v) => {
                  const c = asConfig(v);
                  setProblem(typeof c === "string" ? c : null);
                  if (typeof c !== "string") setDraft((d) => d && { ...d, config: c });
                }}
              />
              {problem && <p role="alert">{problem}</p>}
              <MemberSettings project={project} name={name} admin={admin} />
            </>
          )}
          <StateView project={project} name={name} members={member ? [member] : (n.locations ?? [])} />
          {managed && <Addresses project={project} name={name} />}
          <h2>Used by</h2>
          {(n.used_by ?? []).length === 0 ? (
            <p>Nothing.</p>
          ) : (
            <ul>
              {(n.used_by ?? []).map((u) => (
                <li key={u}>
                  <UsedBy url={u} />
                </li>
              ))}
            </ul>
          )}
        </>
      )}
      {deleting && (
        <Confirm
          title={`Delete network ${name}?`}
          confirmLabel="Delete"
          danger
          onCancel={() => setDeleting(false)}
          onConfirm={() => {
            setDeleting(false);
            void run(`Delete network ${name}`, () => deleteNetwork(project, name)).then((ok) => ok && navigate("/networks"));
          }}
        >
          <p>Networks still used by instances, profiles or other networks can't be deleted.</p>
        </Confirm>
      )}
    </>
  );
}

/** Each member's own keys (parent, external interfaces), editable one member at a time. */
function MemberSettings({ project, name, admin }: { project: string; name: string; admin: boolean }) {
  const { run, version } = useActions();
  const { data } = useApi(`network-members:${project}/${name}:${version}`, () => fetchNetworkMembers(project, name));
  if (!data || data.length === 0) return null;
  return (
    <>
      <h2>Per-member settings</h2>
      <div className="grid">
        {data.map((m) => (
          <MemberRow key={m.member} m={m} admin={admin} onSave={(config) => void run(`Save ${name} on ${m.member}`, () => updateNetwork(project, name, { config }, m.member))} />
        ))}
      </div>
    </>
  );
}

function MemberRow({ m, admin, onSave }: { m: NetworkMember; admin: boolean; onSave: (c: Record<string, string>) => void }) {
  const [config, setConfig] = useState(m.config);
  const [problem, setProblem] = useState<string | null>(null);
  return (
    <div>
      {m.error ? (
        <p role="alert">
          {m.member}: {m.error}
        </p>
      ) : (
        <>
          <YamlEditor
            label={m.member}
            rows={3}
            value={config}
            readOnly={!admin}
            onChange={(v) => {
              const c = asConfig(v);
              setProblem(typeof c === "string" ? c : null);
              if (typeof c !== "string") setConfig(c);
            }}
          />
          {problem && <p role="alert">{problem}</p>}
          {admin && (
            <button type="button" disabled={problem !== null} onClick={() => onSave(config)}>
              Save {m.member}
            </button>
          )}
        </>
      )}
    </div>
  );
}

function StateView({ project, name, members }: { project: string; name: string; members: string[] }) {
  const [picked, setPicked] = useState("");
  const target = picked || members[0];
  const { data, error } = useApi(`network-state:${project}/${name}:${target}`, () => fetchNetworkState(project, name, target), 30_000);
  return (
    <>
      <h2>State</h2>
      {members.length > 1 && (
        <label className="field">
          On member
          <select value={target} onChange={(e) => setPicked(e.target.value)}>
            {members.map((m) => (
              <option key={m}>{m}</option>
            ))}
          </select>
        </label>
      )}
      {error && <p className="muted">State unavailable: {error}</p>}
      {data && <StateList s={data} />}
    </>
  );
}

function StateList({ s }: { s: NetworkState }) {
  const c = s.counters;
  return (
    <div className="hardware">
      <dl>
        <dt>State</dt>
        <dd>
          {s.state} ({s.type})
        </dd>
        <dt>MTU</dt>
        <dd>{s.mtu}</dd>
        <dt>MAC</dt>
        <dd>{s.hwaddr || "–"}</dd>
        <dt>Addresses</dt>
        <dd>{(s.addresses ?? []).map((a) => `${a.address}/${a.netmask}`).join(", ") || "–"}</dd>
        {s.vlan && (
          <>
            <dt>VLAN</dt>
            <dd>
              {s.vlan.vid} on {s.vlan.lower_device}
            </dd>
          </>
        )}
        {s.ovn?.chassis && (
          <>
            <dt>OVN gateway chassis</dt>
            <dd>{s.ovn.chassis}</dd>
          </>
        )}
        {s.bond && (
          <>
            <dt>Bond</dt>
            <dd>
              {s.bond.mode} over {(s.bond.lower_devices ?? []).join(", ")}
            </dd>
          </>
        )}
        {s.bridge && (
          <>
            <dt>Bridge ports</dt>
            <dd>{(s.bridge.upper_devices ?? []).join(", ") || "–"}</dd>
          </>
        )}
        {c && (
          <>
            <dt>Received</dt>
            <dd>
              {formatBytes(c.bytes_received)} ({formatNumber(c.packets_received)} packets)
            </dd>
            <dt>Sent</dt>
            <dd>
              {formatBytes(c.bytes_sent)} ({formatNumber(c.packets_sent)} packets)
            </dd>
          </>
        )}
      </dl>
    </div>
  );
}

function Addresses({ project, name }: { project: string; name: string }) {
  const alloc = useApi(`network-alloc:${project}`, () => fetchNetworkAllocations(project), 60_000);
  const leases = useApi(`network-leases:${project}/${name}`, () => fetchNetworkLeases(project, name), 60_000);
  const mine = (alloc.data ?? []).filter((a) => a.network === name);
  return (
    <>
      <h2>Allocated addresses</h2>
      {alloc.error && <p role="alert">{alloc.error}</p>}
      {alloc.data && mine.length === 0 && <p>None.</p>}
      {mine.length > 0 && (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>Address</th>
                <th>Type</th>
                <th>Used by</th>
                <th>MAC</th>
                <th>NAT</th>
              </tr>
            </thead>
            <tbody>
              {mine.map((a) => (
                <tr key={`${a.addresses}-${a.used_by}`}>
                  <td>{a.addresses}</td>
                  <td>{a.type}</td>
                  <td>
                    <UsedBy url={a.used_by} />
                  </td>
                  <td>{a.hwaddr || "–"}</td>
                  <td>{a.nat ? "yes" : "no"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <h2>DHCP leases</h2>
      {leases.error && <p className="muted">Leases unavailable: {leases.error}</p>}
      {leases.data && leases.data.length === 0 && <p>None.</p>}
      {leases.data && leases.data.length > 0 && (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>Hostname</th>
                <th>Address</th>
                <th>MAC</th>
                <th>Type</th>
                <th>Member</th>
              </tr>
            </thead>
            <tbody>
              {leases.data.map((l) => (
                <tr key={`${l.address}-${l.hwaddr}`}>
                  <td>{l.hostname}</td>
                  <td>{l.address}</td>
                  <td>{l.hwaddr || "–"}</td>
                  <td>{l.type}</td>
                  <td>{l.location ? <MemberLink name={l.location} /> : "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
