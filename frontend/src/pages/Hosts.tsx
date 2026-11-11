import { Link } from "react-router";
import { fetchHosts, type Host } from "../api";
import { formatBytes, formatNumber, formatPercent, formatRate } from "../format";
import { useApi } from "../useApi";
import { HOST_METRICS_DOC, hostMemory } from "./hostValues";

const REFRESH_MS = 15_000;

/** A meter that turns amber from 60% and red from 85% (the browser colours it). */
function Gauge({ value, label }: { value: number; label: string }) {
  return <meter min={0} max={100} low={60} high={85} optimum={0} value={Math.min(value, 100)} aria-label={label} />;
}

const pct = (part: number, whole: number) => (whole ? (part / whole) * 100 : 0);

function Cpu({ h }: { h: Host }) {
  const n = h.node;
  if (n?.ok && n.cpu_percent != null) {
    const extra = [
      (n.cpu_iowait_percent ?? 0) >= 5 && `iowait ${formatPercent(n.cpu_iowait_percent)}`,
      (n.cpu_steal_percent ?? 0) >= 5 && `steal ${formatPercent(n.cpu_steal_percent)}`,
    ].filter(Boolean);
    return (
      <>
        <Gauge value={n.cpu_percent} label={`${h.name} CPU`} /> {formatPercent(n.cpu_percent)}
        {extra.length > 0 && <span className="muted"> ({extra.join(", ")})</span>}
      </>
    );
  }
  if (h.load1 == null || !h.cpus) return <>–</>;
  return (
    <>
      <Gauge value={pct(h.load1, h.cpus)} label={`${h.name} load per CPU`} /> load {formatNumber(h.load1)} / {h.cpus}
    </>
  );
}

function Memory({ h }: { h: Host }) {
  const m = hostMemory(h);
  if (!m) return <>–</>;
  return (
    <>
      <Gauge value={pct(m.used, m.total)} label={`${h.name} memory`} /> {m.estimated ? "≈" : ""}
      {formatBytes(m.used)} / {formatBytes(m.total)}
    </>
  );
}

function Swap({ h }: { h: Host }) {
  const n = h.node;
  const total = n?.ok && n.swap_total != null ? n.swap_total : h.swap_total;
  const used =
    n?.ok && n.swap_used != null
      ? n.swap_used
      : h.swap_total != null && h.swap_free != null
        ? h.swap_total - h.swap_free
        : null;
  if (!total) return <span className="muted">none</span>;
  return <>{`${formatBytes(used)} / ${formatBytes(total)}`}</>;
}

function Disk({ h }: { h: Host }) {
  const n = h.node;
  if (!n?.ok) return <>–</>;
  return (
    <>
      {n.rootfs_size ? (
        <>
          <Gauge value={pct(n.rootfs_used ?? 0, n.rootfs_size)} label={`${h.name} root filesystem`} /> /{" "}
          {formatPercent(pct(n.rootfs_used ?? 0, n.rootfs_size))}
        </>
      ) : null}
      {n.disk_busy_percent != null && (
        <span className="muted">
          {" "}
          · {n.disk_busiest} {formatPercent(n.disk_busy_percent)} busy
        </span>
      )}
    </>
  );
}

function Allocated({ h }: { h: Host }) {
  const ratio = h.cpus ? h.allocated_cpus / h.cpus : null;
  return (
    <>
      {h.allocated_cpus} vCPU{h.cpus ? ` / ${h.cpus}` : ""}
      {ratio != null && ratio > 1 && <strong> ({ratio.toFixed(1)}×)</strong>}, {formatBytes(h.allocated_memory)}
      {h.memory_total ? ` / ${formatBytes(h.memory_total)}` : ""}
      {h.unlimited > 0 && <span className="muted"> + {h.unlimited} unlimited</span>}
    </>
  );
}

export function HostsTable({ hosts }: { hosts: Host[] }) {
  return (
    <div className="scroll">
      <table>
        <thead>
          <tr>
            <th>Host</th>
            <th>CPU</th>
            <th>Memory</th>
            <th>Swap</th>
            <th>Disk</th>
            <th>Instances</th>
            <th>Instances use</th>
            <th>Allocated (running)</th>
            <th>Network rx / tx</th>
          </tr>
        </thead>
        <tbody>
          {hosts.map((h) => (
            <tr key={h.name}>
              <td>
                <Link to={`/members/${encodeURIComponent(h.name)}`}>{h.name}</Link>
                {h.status !== "Online" && <span role="alert"> {h.status}</span>}
              </td>
              <td>
                <Cpu h={h} />
              </td>
              <td>
                <Memory h={h} />
              </td>
              <td>
                <Swap h={h} />
              </td>
              <td>
                <Disk h={h} />
              </td>
              <td className="num">
                {h.instances_running} / {h.instances}
              </td>
              <td>
                {formatNumber(h.instances_cpu)} CPUs, {formatBytes(h.instances_memory)}
              </td>
              <td>
                <Allocated h={h} />
              </td>
              <td>
                {formatRate(h.net_rx_bps)} / {formatRate(h.net_tx_bps)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** What node_exporter adds, and where it's missing or failing. */
function NodeNote({ hosts, enabled }: { hosts: Host[]; enabled: boolean }) {
  if (!enabled)
    return (
      <p className="muted">
        Host metrics from node_exporter are turned off; CPU is shown as load and memory is estimated.
      </p>
    );
  const missing = hosts.filter((h) => h.node?.absent).map((h) => h.name);
  const failing = hosts.filter((h) => h.node && !h.node.ok && !h.node.absent);
  return (
    <>
      {failing.map((h) => (
        <p key={h.name} role="alert">
          node_exporter on {h.name}: {h.node!.error}
        </p>
      ))}
      {missing.length > 0 && (
        <p className="muted">
          No node_exporter on {missing.join(", ")}: CPU is shown as load per CPU and memory (≈) includes page cache.
          Install it for real CPU, memory, disk and pressure figures ({HOST_METRICS_DOC}).
        </p>
      )}
    </>
  );
}

export default function HostsSection() {
  const { data, error } = useApi("hosts", fetchHosts, REFRESH_MS);
  return (
    <section aria-label="Hosts">
      <h2>Hosts</h2>
      {error && <p role="alert">{error}</p>}
      {!data && !error && <p role="status">Loading hosts…</p>}
      {data && (
        <>
          <HostsTable hosts={data.hosts} />
          <NodeNote hosts={data.hosts} enabled={data.node_exporter} />
        </>
      )}
    </section>
  );
}
