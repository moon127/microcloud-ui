import { useRef, useState } from "react";
import { Link } from "react-router";
import { fetchHosts, type Host } from "../api";
import { formatBytes, formatNumber, formatPair, formatPercent, formatRate } from "../format";
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
        {extra.length > 0 && <span className="sub">{extra.join(", ")}</span>}
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
      {formatPair(formatBytes(m.used), formatBytes(m.total))}
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
  return <>{formatPair(formatBytes(used), formatBytes(total))}</>;
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
        <span className="sub">
          {n.disk_busiest} {formatPercent(n.disk_busy_percent)} busy
        </span>
      )}
    </>
  );
}

function Allocated({ h, onUnlimited }: { h: Host; onUnlimited: (host: string) => void }) {
  const ratio = h.cpus ? h.allocated_cpus / h.cpus : null;
  return (
    <>
      {h.allocated_cpus} vCPU{h.cpus ? ` / ${h.cpus}` : ""}
      {ratio != null && ratio > 1 && <strong> ({ratio.toFixed(1)}×)</strong>},{" "}
      {h.memory_total
        ? formatPair(formatBytes(h.allocated_memory), formatBytes(h.memory_total))
        : formatBytes(h.allocated_memory)}
      {h.unlimited > 0 && (
        <span className="sub">
          +{" "}
          <button type="button" className="link" onClick={() => onUnlimited(h.name)}>
            {h.unlimited} unlimited
          </button>
        </span>
      )}
    </>
  );
}

export function HostsTable({ hosts, onUnlimited }: { hosts: Host[]; onUnlimited: (host: string) => void }) {
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
            <th className="num">Instances</th>
            <th>Instances use</th>
            <th>Allocated (running)</th>
            <th>Net rx / tx</th>
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
                <Allocated h={h} onUnlimited={onUnlimited} />
              </td>
              <td>
                {formatPair(formatRate(h.net_rx_bps), formatRate(h.net_tx_bps))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const MISSING = { cpu: "CPU", memory: "memory" } as const;

/** Running instances with no CPU and/or memory limit, each a link to where limits are set. */
function Unlimited({ hosts, host, open, onToggle }: { hosts: Host[]; host: string | null; open: boolean; onToggle: (open: boolean, host?: string | null) => void }) {
  const rows = hosts.flatMap((h) => h.unlimited_instances.map((i) => ({ ...i, host: h.name })));
  if (rows.length === 0) return null;
  const shown = host ? rows.filter((r) => r.host === host) : rows;
  return (
    <details open={open} onToggle={(e) => onToggle(e.currentTarget.open)}>
      <summary>
        {rows.length} running instance{rows.length === 1 ? "" : "s"} without a CPU or memory limit
        {host && open && <> (on {host})</>}
      </summary>
      <p className="muted">
        These can use all of their host&apos;s CPUs or memory, so they aren&apos;t counted in Allocated. Set limits on
        the instance, or on a profile it uses.
      </p>
      <div className="scroll">
        <table>
          <thead>
            <tr>
              <th>Instance</th>
              <th>Project</th>
              <th>Host</th>
              <th>No limit on</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => {
              const to = `/instances/${encodeURIComponent(r.project)}/${encodeURIComponent(r.name)}`;
              return (
                <tr key={`${r.project}/${r.name}`}>
                  <td>
                    <Link to={to}>{r.name}</Link>
                  </td>
                  <td>{r.project}</td>
                  <td>{r.host}</td>
                  <td>{r.missing.map((m) => MISSING[m]).join(", ")}</td>
                  <td>
                    <Link to={`${to}?tab=config`} aria-label={`Set limits on ${r.name}`}>
                      Set limits
                    </Link>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {host && shown.length < rows.length && (
        <button type="button" className="link" onClick={() => onToggle(true, null)}>
          Show all hosts
        </button>
      )}
    </details>
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
  const [unlimited, setUnlimited] = useState<{ open: boolean; host: string | null }>({ open: false, host: null });
  const list = useRef<HTMLDivElement>(null);
  const showUnlimited = (host: string) => {
    setUnlimited({ open: true, host });
    requestAnimationFrame(() => list.current?.scrollIntoView?.({ block: "nearest" }));
  };
  return (
    <section aria-label="Hosts">
      <h2>Hosts</h2>
      {error && <p role="alert">{error}</p>}
      {!data && !error && <p role="status">Loading hosts…</p>}
      {data && (
        <>
          <HostsTable hosts={data.hosts} onUnlimited={showUnlimited} />
          <div ref={list}>
            <Unlimited
              hosts={data.hosts}
              host={unlimited.host}
              open={unlimited.open}
              onToggle={(open, host = open ? unlimited.host : null) => setUnlimited({ open, host })}
            />
          </div>
          <NodeNote hosts={data.hosts} enabled={data.node_exporter} />
        </>
      )}
    </section>
  );
}
