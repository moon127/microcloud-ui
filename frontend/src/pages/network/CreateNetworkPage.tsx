import { useState } from "react";
import { useNavigate } from "react-router";
import { stringify } from "yaml";
import { createNetwork, fetchMembers, fetchNetworks, fetchServer, type NetworkType } from "../../api";
import { useActions } from "../../actions";
import { useProject } from "../../project";
import { useSession } from "../../session";
import { useApi } from "../../useApi";
import YamlEditor from "../../YamlEditor";
import { asConfig } from "../instance/state";
import { networkPath } from "./paths";
import { buildNetwork, emptyNetwork, fieldsFor, MEMBER_KEY, networkProblems, type NetworkForm } from "./request";

const TYPES: { type: NetworkType; label: string; note: string }[] = [
  { type: "bridge", label: "Bridge", note: "A Linux bridge on each member, with LXD's DHCP/DNS and NAT" },
  { type: "ovn", label: "OVN", note: "A virtual network spanning the cluster, routed out through an uplink" },
  { type: "physical", label: "Physical", note: "An existing host interface, typically the uplink for OVN networks" },
  { type: "macvlan", label: "Macvlan", note: "Instances get their own MAC on a host interface" },
];

export default function CreateNetworkPage() {
  const { user } = useSession();
  const { run } = useActions();
  const navigate = useNavigate();
  const { project: current } = useProject();
  const [f, setF] = useState<NetworkForm>(() => emptyNetwork(current || "default"));
  const [tried, setTried] = useState(false);
  const [extraProblem, setExtraProblem] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const members = useApi("members", fetchMembers);
  const names = (members.data ?? []).map((m) => m.name);
  const networks = useApi(`networks:${f.project}`, () => fetchNetworks(f.project));
  // each member's own interfaces, offered as parents
  const ifaces = useApi(`ifaces:${f.project}:${names.join(",")}`, () =>
    Promise.all(names.map((m) => fetchNetworks(f.project, m).then((ns) => [m, ns.filter((n) => !n.managed)] as const))),
  );
  const features = useApi("server", fetchServer).data?.features ?? {};

  if (user.role !== "admin") return <p role="alert">Creating networks needs the admin role.</p>;

  const set = <K extends keyof NetworkForm>(k: K, v: NetworkForm[K]) => setF((x) => ({ ...x, [k]: v }));
  const setField = (k: string, v: string) => set("fields", { ...f.fields, [k]: v });
  const issues = [...networkProblems(f, names), ...(extraProblem ? [extraProblem] : [])];
  const req = buildNetwork(f, names, features);
  const mk = MEMBER_KEY[f.type];
  const uplinks = (networks.data ?? []).filter((n) => n.managed && n.type === "physical");
  const first = names.length ? (f.perMember[names[0]] ?? "") : "";

  return (
    <form
      className="create"
      aria-label="Create network"
      onSubmit={(e) => {
        e.preventDefault();
        setTried(true);
        if (issues.length) return;
        setError(null);
        void run(`Create network ${req.name}`, () => createNetwork(req), { onError: setError }).then(
          (ok) => ok && navigate(networkPath(req.project, req.name)),
        );
      }}
    >
      <h1>Create network</h1>
      <section aria-label="Basics">
        <div className="form-row">
          <label className="field">
            Name
            <input value={f.name} onChange={(e) => set("name", e.target.value)} />
          </label>
          <label className="field">
            Project
            <input value={f.project} readOnly />
          </label>
        </div>
        <fieldset aria-label="Type">
          {TYPES.map((t) => (
            <label key={t.type}>
              <input type="radio" checked={f.type === t.type} onChange={() => set("type", t.type)} /> {t.label}{" "}
              <span className="muted">— {t.note}</span>
            </label>
          ))}
        </fieldset>
        <label className="field">
          Description
          <input value={f.description} onChange={(e) => set("description", e.target.value)} />
        </label>
      </section>

      {f.type === "ovn" && (
        <section aria-label="Uplink">
          <label className="field">
            Uplink network
            <select value={f.fields.network ?? ""} onChange={(e) => setField("network", e.target.value)}>
              <option value="">Choose…</option>
              {uplinks.map((n) => (
                <option key={n.name}>{n.name}</option>
              ))}
            </select>
          </label>
        </section>
      )}

      {mk && (
        <section aria-label="Per member">
          <h2>{mk.label}</h2>
          <p className="muted">Each member has its own interfaces, so this is set per member.</p>
          {names.map((m) => {
            const list = ifaces.data?.find(([n]) => n === m)?.[1] ?? [];
            return (
              <div key={m}>
                <label className="field">
                  {m}
                  <input
                    list={`ifaces-${m}`}
                    value={f.perMember[m] ?? ""}
                    onChange={(e) => set("perMember", { ...f.perMember, [m]: e.target.value })}
                  />
                </label>
                <datalist id={`ifaces-${m}`}>
                  {list.map((n) => (
                    <option key={n.name} value={n.name}>
                      {n.type}
                    </option>
                  ))}
                </datalist>
              </div>
            );
          })}
          {names.length > 1 && (
            <button
              type="button"
              disabled={!first.trim()}
              onClick={() => set("perMember", Object.fromEntries(names.map((m) => [m, first])))}
            >
              Use {names[0]}'s value on every member
            </button>
          )}
        </section>
      )}

      <section aria-label="Settings">
        <h2>Settings</h2>
        <p className="muted">Leave blank for LXD's default.</p>
        <div className="form-row">
          {fieldsFor(f.type, features).map((fd) => (
            <label key={fd.key} className="field">
              {fd.label}
              {fd.toggle ? (
                <select value={f.fields[fd.key] ?? ""} onChange={(e) => setField(fd.key, e.target.value)}>
                  <option value="">Default</option>
                  <option value="true">On</option>
                  <option value="false">Off</option>
                </select>
              ) : (
                <input placeholder={fd.placeholder} value={f.fields[fd.key] ?? ""} onChange={(e) => setField(fd.key, e.target.value)} />
              )}
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
      {error && <p role="alert">Creating the network failed: {error}</p>}
      <div className="actions">
        <button type="submit" className="primary">
          Create
        </button>
        <button type="button" onClick={() => navigate("/networks")}>
          Cancel
        </button>
      </div>
    </form>
  );
}
