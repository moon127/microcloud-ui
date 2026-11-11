import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import {
  deleteNetwork,
  fetchAcls,
  fetchNetwork,
  fetchNetworkAllocations,
  fetchNetworkLeases,
  fetchNetworkMembers,
  fetchNetworkState,
  updateNetwork,
  type NetworkState,
} from "../../api";
import { useActions } from "../../actions";
import Confirm from "../../Confirm";
import { formatBytes, formatNumber } from "../../format";
import { MemberLink, UsedBy } from "../../links";
import { useSession } from "../../session";
import { useApi } from "../../useApi";
import MemberConfig from "../../MemberConfig";
import YamlEditor from "../../YamlEditor";
import { asConfig } from "../instance/state";
import Forwards from "./Forwards";
import { aclPath } from "./paths";

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
              {(n.type === "bridge" || n.type === "ovn") && (
                <AclAssignment
                  project={project}
                  config={draft.config}
                  readOnly={!admin}
                  onChange={(config) => setDraft((d) => d && { ...d, config })}
                />
              )}
              <MemberSettings project={project} name={name} admin={admin} />
              {(n.type === "bridge" || n.type === "ovn") && (
                <Forwards project={project} network={name} admin={admin} members={n.type === "bridge" ? (n.locations ?? []) : []} />
              )}
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
          <MemberConfig key={m.member} {...m} admin={admin} onSave={(config) => void run(`Save ${name} on ${m.member}`, () => updateNetwork(project, name, { config }, m.member))} />
        ))}
      </div>
    </>
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

const ACL_KEYS = ["security.acls.default.ingress.action", "security.acls.default.egress.action"] as const;

/** The network's ``security.acls`` keys as a form; changes go into the draft, saved with Save. */
function AclAssignment({
  project,
  config,
  readOnly,
  onChange,
}: {
  project: string;
  config: Record<string, string>;
  readOnly: boolean;
  onChange: (c: Record<string, string>) => void;
}) {
  const acls = useApi(`acls:${project}`, () => fetchAcls(project));
  const chosen = (config["security.acls"] ?? "")
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);
  const names = [...new Set([...(acls.data ?? []).map((a) => a.name), ...chosen])];
  const setKey = (k: string, v: string) => {
    const c = { ...config };
    if (v) c[k] = v;
    else delete c[k];
    onChange(c);
  };
  return (
    <section aria-label="ACLs">
      <h2>ACLs</h2>
      {acls.error && <p role="alert">{acls.error}</p>}
      {names.length === 0 && (
        <p className="muted">
          No ACLs in {project}. <Link to="/network-acls">Network ACLs</Link>
        </p>
      )}
      <fieldset disabled={readOnly} className="inline">
        {names.map((a) => (
          <label key={a}>
            <input
              type="checkbox"
              checked={chosen.includes(a)}
              onChange={(e) => setKey("security.acls", (e.target.checked ? [...chosen, a] : chosen.filter((x) => x !== a)).join(","))}
            />{" "}
            <Link to={aclPath(project, a)}>{a}</Link>
          </label>
        ))}
      </fieldset>
      <fieldset disabled={readOnly} className="inline">
        {ACL_KEYS.map((k) => (
          <label key={k} className="field">
            Default {k.includes("ingress") ? "ingress" : "egress"} action
            <select value={config[k] ?? ""} onChange={(e) => setKey(k, e.target.value)}>
              <option value="">LXD default (reject)</option>
              <option>allow</option>
              <option>reject</option>
              <option>drop</option>
            </select>
          </label>
        ))}
      </fieldset>
      {!readOnly && <p className="muted">Save the network to apply.</p>}
    </section>
  );
}
