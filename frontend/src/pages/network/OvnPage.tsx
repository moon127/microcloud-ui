import { Link } from "react-router";
import { fetchOvn, type OvnNetwork, type OvnUplink } from "../../api";
import { MemberLink } from "../../links";
import { useApi } from "../../useApi";
import { aclPath, networkPath } from "./paths";

const count = (x: unknown[] | null) => (x === null ? "?" : String(x.length));

/** Problems worth a look: networks not created, without a gateway chassis, uplink ranges nearly used up. */
function problems(networks: OvnNetwork[], uplinks: OvnUplink[]): string[] {
  const out: string[] = [];
  for (const n of networks) {
    if (n.status && n.status !== "Created") out.push(`${n.name} (${n.project}) is ${n.status}`);
    if (!n.chassis) out.push(`${n.name} (${n.project}) has no active gateway chassis${n.state_error ? `: ${n.state_error}` : ""}`);
  }
  for (const u of uplinks) {
    if (u.ipv4_size && u.ipv4_used / u.ipv4_size >= 0.8)
      out.push(`${u.name}: ${u.ipv4_used} of ${u.ipv4_size} OVN router addresses in use`);
  }
  return out;
}

export default function OvnPage() {
  const { data, error } = useApi("ovn", fetchOvn, 60_000);
  const issues = data ? problems(data.networks, data.uplinks) : [];
  return (
    <>
      <h1>OVN</h1>
      <p className="muted">
        From LXD's view of its OVN networks. <Link to="/networks">Networks</Link>
      </p>
      {error && <p role="alert">{error}</p>}
      {!data && !error && <p role="status">Loading…</p>}
      {data && data.networks.length === 0 && data.uplinks.length === 0 && <p>No OVN networks or uplinks.</p>}
      {data && (data.networks.length > 0 || data.uplinks.length > 0) && (
        <>
          {issues.length === 0 ? (
            <p role="status">
              <strong>All {data.networks.length} OVN networks have an active gateway chassis.</strong>
            </p>
          ) : (
            <div role="alert">
              <ul aria-label="Problems">
                {issues.map((i) => (
                  <li key={i}>{i}</li>
                ))}
              </ul>
            </div>
          )}
          <h2>Uplinks</h2>
          {data.uplinks.length === 0 && <p>None.</p>}
          {data.uplinks.map((u) => (
            <section key={u.name} aria-label={`Uplink ${u.name}`} className="hardware">
              <h3>
                <Link to={networkPath("default", u.name)}>{u.name}</Link> <span className="muted">{u.status}</span>
              </h3>
              <dl>
                <dt>Gateway</dt>
                <dd>{[u.ipv4_gateway, u.ipv6_gateway].filter(Boolean).join(", ") || "–"}</dd>
                <dt>OVN router addresses</dt>
                <dd>
                  {u.ipv4_size ? (
                    <>
                      <meter min={0} max={u.ipv4_size} high={u.ipv4_size * 0.8} value={u.ipv4_used} aria-label={`${u.name} router addresses`} />{" "}
                      {u.ipv4_used} of {u.ipv4_size} used ({u.ipv4_ranges})
                    </>
                  ) : (
                    u.ipv4_ranges || "–"
                  )}
                  {u.ipv6_ranges && <div>IPv6: {u.ipv6_ranges}</div>}
                </dd>
                <dt>Routes for OVN networks</dt>
                <dd>{u.ipv4_routes || "–"}</dd>
                <dt>DNS</dt>
                <dd>{u.dns || "–"}</dd>
                <dt>BGP peers</dt>
                <dd>{u.bgp_peers.join(", ") || "–"}</dd>
                <dt>OVN networks</dt>
                <dd>{u.networks.join(", ") || "none"}</dd>
              </dl>
            </section>
          ))}
          <h2>OVN networks</h2>
          {data.networks.length === 0 && <p>None.</p>}
          {data.networks.length > 0 && (
            <div className="scroll">
              <table>
                <thead>
                  <tr>
                    <th>Network</th>
                    <th>Project</th>
                    <th>Uplink</th>
                    <th>Subnets</th>
                    <th>Router address</th>
                    <th>Gateway chassis</th>
                    <th>Forwards</th>
                    <th>LBs</th>
                    <th>Peers</th>
                    <th>ACLs</th>
                    <th>Allocated</th>
                  </tr>
                </thead>
                <tbody>
                  {data.networks.map((n) => (
                    <tr key={`${n.project}/${n.name}`}>
                      <td>
                        <Link to={networkPath(n.project, n.name)}>{n.name}</Link>
                        {n.status !== "Created" && <span role="alert"> {n.status}</span>}
                      </td>
                      <td>{n.project}</td>
                      <td>{n.uplink || "–"}</td>
                      <td>
                        {[n.ipv4 && `${n.ipv4}${n.ipv4_nat ? " (NAT)" : ""}`, n.ipv6 && `${n.ipv6}${n.ipv6_nat ? " (NAT)" : ""}`]
                          .filter(Boolean)
                          .join(", ") || "–"}
                      </td>
                      <td>{[n.router_ipv4, n.router_ipv6].filter(Boolean).join(", ") || "–"}</td>
                      <td>{n.chassis ? <MemberLink name={n.chassis} /> : <span className="muted">none</span>}</td>
                      <td className="num">{count(n.forwards)}</td>
                      <td className="num">{count(n.load_balancers)}</td>
                      <td className="num">{count(n.peers)}</td>
                      <td>
                        {n.acls.length
                          ? n.acls.map((a, i) => (
                              <span key={a}>
                                {i > 0 && ", "}
                                <Link to={aclPath(n.project, a)}>{a}</Link>
                              </span>
                            ))
                          : "–"}
                      </td>
                      <td>
                        {Object.entries(n.allocations)
                          .map(([k, v]) => `${v} ${k}`)
                          .join(", ") || "–"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </>
  );
}
