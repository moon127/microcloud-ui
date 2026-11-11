import { useState } from "react";
import { Link } from "react-router";
import { fetchMembers, fetchNetworks, fetchNetworkTraffic, type Network, type Series } from "../api";
import { useActions } from "../actions";
import MetricPanel from "../chart/MetricPanel";
import { nonZero, refreshFor, type PanelQuery } from "../chart/panel";
import TimeChart from "../chart/TimeChart";
import { formatRate } from "../format";
import { useRange } from "../range";
import RangePicker from "../RangePicker";
import { useProject } from "../project";
import { useSession } from "../session";
import { networkPath } from "./network/paths";
import { useApi } from "../useApi";

const label = (suffix: string) => (s: Series) => ({ label: `${s.labels.network} ${suffix}`, points: s.points });

export default function NetworksPage() {
  const range = useRange();
  const traffic = useApi(`net-traffic:${range}`, () => fetchNetworkTraffic(range), refreshFor(range));
  const host: PanelQuery[] = [
    {
      name: "mcui_host_network_receive_bytes_total",
      fn: "rate",
      label: (l) => `${l.member} ${l.network} rx`,
    },
    {
      name: "mcui_host_network_transmit_bytes_total",
      fn: "rate",
      label: (l) => `${l.member} ${l.network} tx`,
    },
  ];
  return (
    <>
      <h1>Networks</h1>
      <NetworkList />
      <h2>Traffic</h2>
      <RangePicker />
      <h3>LXD networks (from instance NICs)</h3>
      <p className="muted">
        OVN networks don't report their own counters, so traffic is the sum of the instances' NICs on each network.
      </p>
      {traffic.error && <p role="alert">{traffic.error}</p>}
      {traffic.data && (
        <div className="grid">
          <TimeChart
            title="Received by instances"
            format={formatRate}
            series={traffic.data.rx.map(label("rx"))}
          />
          <TimeChart
            title="Sent by instances"
            format={formatRate}
            series={traffic.data.tx.map(label("tx"))}
          />
        </div>
      )}
      <h3>Host interfaces</h3>
      <div className="grid">
        <MetricPanel title="Host interface traffic" range={range} format={formatRate} queries={host} compute={nonZero(host)} />
      </div>
    </>
  );
}

/** What a network sits on: OVN's uplink, a physical/macvlan parent, or its addresses. */
function summary(n: Network): string {
  const c = n.config;
  if (n.type === "ovn") return [c.network && `uplink ${c.network}`, c["ipv4.address"], c["ipv6.address"]].filter(Boolean).join(" · ");
  if (n.type === "physical")
    return [c["ipv4.gateway"] && `gateway ${c["ipv4.gateway"]}`, c["ipv4.ovn.ranges"] && `OVN ${c["ipv4.ovn.ranges"]}`]
      .filter(Boolean)
      .join(" · ");
  return [c["ipv4.address"], c["ipv6.address"], c.parent].filter(Boolean).join(" · ");
}

function NetworkList() {
  const { user } = useSession();
  const { version } = useActions();
  const project = useProject().project || "default";
  const nets = useApi(`networks:${project}:${version}`, () => fetchNetworks(project), 60_000);
  const members = useApi("members", fetchMembers);
  const [member, setMember] = useState("");
  const target = member || members.data?.[0]?.name;
  const local = useApi(`networks:${project}:${target}`, () => (target ? fetchNetworks(project, target) : Promise.resolve([])));
  const managed = (nets.data ?? []).filter((n) => n.managed);
  const unmanaged = (local.data ?? []).filter((n) => !n.managed);
  return (
    <>
      <h2>LXD networks in {project}</h2>
      <p className="actions">
        {user.role === "admin" && (
          <Link className="button primary" to="/networks/new">
            Create network
          </Link>
        )}
        <Link to="/network-acls">Network ACLs</Link>
      </p>
      {nets.error && <p role="alert">{nets.error}</p>}
      {!nets.data && !nets.error && <p role="status">Loading…</p>}
      {nets.data && managed.length === 0 && <p>No managed networks.</p>}
      {managed.length > 0 && (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Type</th>
                <th>Status</th>
                <th>Details</th>
                <th>Used by</th>
                <th>Description</th>
              </tr>
            </thead>
            <tbody>
              {managed.map((n) => (
                <tr key={n.name}>
                  <td>
                    <Link to={networkPath(project, n.name)}>{n.name}</Link>
                  </td>
                  <td>{n.type}</td>
                  <td>{n.status}</td>
                  <td>{summary(n) || "–"}</td>
                  <td className="num">{n.used_by ?? 0}</td>
                  <td>{n.description}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <h2>Host interfaces</h2>
      {(members.data ?? []).length > 1 && (
        <label className="field">
          Member
          <select value={target} onChange={(e) => setMember(e.target.value)}>
            {(members.data ?? []).map((m) => (
              <option key={m.name}>{m.name}</option>
            ))}
          </select>
        </label>
      )}
      {local.error && <p role="alert">{local.error}</p>}
      {unmanaged.length > 0 && (
        <p className="chips">
          {unmanaged.map((n) => (
            <Link key={n.name} className="chip" to={networkPath(project, n.name, target)}>
              {n.name} <span className="muted">{n.type}</span>
            </Link>
          ))}
        </p>
      )}
    </>
  );
}
