import { useState } from "react";
import { Link } from "react-router";
import { fetchCeph, fetchMembers, fetchRbdTop, type CephMon, type CephOsd, type CephState, type RbdMetric, type RbdRow } from "../api";
import MetricPanel from "../chart/MetricPanel";
import { ratioSeries } from "../chart/panel";
import { useHashScroll } from "../hashScroll";
import { formatBytes, formatNumber, formatPercent, formatRate } from "../format";
import { MemberLink } from "../links";
import { instancePath } from "../paths";
import { useRange } from "../range";
import RangePicker from "../RangePicker";
import { useApi } from "../useApi";
import { volumePath } from "./storage/paths";

const perSecond = (v: number) => `${formatNumber(v)}/s`;
const ms = (v: number) => `${v.toFixed(v < 10 ? 1 : 0)} ms`;
const pct = (used: number, total: number) => (total ? (used / total) * 100 : 0);
// Ceph's defaults, for a summary from before the backend reported the cluster's own
const RATIOS = { nearfull: 0.85, full: 0.95, default: true };

/** An OSD's % used, flagged once it passes the cluster's nearfull or full ratio. */
function OsdUsed({ percent, nearfull, full }: { percent: number; nearfull: number; full: number }) {
  const state = percent >= full * 100 ? "full" : percent >= nearfull * 100 ? "nearfull" : "";
  return state ? (
    <strong role="alert">
      {formatPercent(percent)} {state}
    </strong>
  ) : (
    <>{formatPercent(percent)}</>
  );
}
const count = (v: number) => Math.round(v).toLocaleString("en");
// Ceph's defaults (mon_data_avail_warn, mon_data_avail_crit): MON_DISK_LOW at 30% free or
// less, MON_DISK_CRIT at 5% or less, in whole percent (the backend says which applies)
const MON_WARN = 30;
const MON_CRIT = 5;

export default function CephPage() {
  const range = useRange();
  const { data, error } = useApi("ceph", fetchCeph, 15_000);
  useHashScroll(data?.ok ? data : null);
  return (
    <>
      <h1>Ceph</h1>
      {error && <p role="alert">{error}</p>}
      {data && !data.enabled && (
        <p>
          Ceph metrics are turned off (<code>[ceph] enabled = false</code>). See <code>docs/CEPH-METRICS.md</code>.
        </p>
      )}
      {data?.enabled && !data.ok && (
        <>
          <p role="alert">No data from ceph-mgr: {data.error}</p>
          <p className="muted">
            Check that the mgr <code>prometheus</code> module is on and port 9283 is reachable (<code>docs/CEPH-METRICS.md</code>).
          </p>
        </>
      )}
      {data?.ok && <Now s={data} />}
      {data?.enabled && (
        <>
          <h2>History</h2>
          <RangePicker />
          <History range={range} pools={data.pools ?? []} perfCounters={data.perf_counters !== false} />
          <Rbd range={range} />
        </>
      )}
    </>
  );
}

function Now({ s }: { s: CephState }) {
  const members = useApi("members", fetchMembers);
  const names = new Set((members.data ?? []).map((m) => m.name));
  const osds = s.osds ?? [];
  const mons = s.mons ?? [];
  const up = osds.filter((o) => o.up).length;
  const inn = osds.filter((o) => o.in).length;
  const cap = s.capacity ?? { total: null, used_raw: null };
  const ratios = s.full_ratios ?? RATIOS;
  const states = Object.entries(s.pgs?.states ?? {});
  const hosts = hostFill(osds);
  const avg = pct(
    hosts.reduce((a, h) => a + h.used, 0),
    hosts.reduce((a, h) => a + h.size, 0),
  );
  // most clusters never change it: shown once an OSD that's in has another (out is 0)
  const reweighted = osds.some((o) => o.in !== false && o.weight != null && o.weight !== 1);
  const health = s.health?.status ?? "UNKNOWN";
  return (
    <>
      <p className={`health ${health.toLowerCase()}`} role="status">
        <strong>{health}</strong>
        {s.version && <span className="muted"> · Ceph {s.version}</span>}
        {s.mgr && <span className="muted"> · from {s.mgr}</span>}
      </p>
      {(s.health?.checks ?? []).length > 0 && (
        <ul aria-label="Health checks">
          {s.health!.checks.map((c) => (
            <li key={c.name}>
              <code>{c.name}</code> <span className="muted">{c.severity}</span>
            </li>
          ))}
        </ul>
      )}
      <div className="hardware">
        <dl>
          <dt>Monitors in quorum</dt>
          <dd>
            {mons.filter((m) => m.in_quorum).length} / {mons.length}
            {mons.some((m) => !m.in_quorum) && (
              <span role="alert"> (out: {mons.filter((m) => !m.in_quorum).map((m) => m.name).join(", ")})</span>
            )}
          </dd>
          <dt>Active manager</dt>
          <dd>{(s.mgrs ?? []).find((m) => m.active)?.name ?? "–"}</dd>
          <dt>OSDs</dt>
          <dd>
            {up} up, {inn} in, of {osds.length}
          </dd>
          <dt>Placement groups</dt>
          <dd>
            {count(s.pgs?.total ?? 0)}: {states.map(([k, v]) => `${count(v)} ${k}`).join(", ") || "–"}
          </dd>
          <dt>Raw capacity</dt>
          <dd>
            {cap.total != null && cap.used_raw != null ? (
              <>
                <meter min={0} max={100} high={80} value={pct(cap.used_raw, cap.total)} aria-label="Raw used" /> {formatBytes(cap.used_raw)} of{" "}
                {formatBytes(cap.total)} used ({pct(cap.used_raw, cap.total).toFixed(0)}%)
              </>
            ) : (
              "–"
            )}
          </dd>
        </dl>
      </div>
      <Monitors mons={mons} members={names} />
      <h2 id="osds">OSDs</h2>
      {s.perf_counters === false ? (
        <PerfCountersHint />
      ) : (
        <p className="muted">
          Nearfull at {formatPercent(ratios.nearfull * 100)}, full at {formatPercent(ratios.full * 100)} (
          {ratios.default ? "Ceph's defaults; this Ceph doesn't report its own" : "this cluster's settings"}). Ceph warns
          when an OSD passes nearfull and stops writes to the cluster once one is full.
        </p>
      )}
      <div className="scroll">
        <table>
          <thead>
            <tr>
              <th>OSD</th>
              <th>Host</th>
              <th>Class</th>
              <th>State</th>
              {reweighted && <th className="num">Reweight</th>}
              <th className="num">PGs</th>
              {s.perf_counters !== false && (
                <>
                  <th className="num">Used</th>
                  <th className="num">Size</th>
                  <th className="num">% used</th>
                  <th />
                  <th className="num">
                    <abbr title="Variance: % used against the cluster's average, as ceph osd df shows it">VAR</abbr>
                  </th>
                </>
              )}
              <th className="num">Apply</th>
              <th className="num">Commit</th>
            </tr>
          </thead>
          <tbody>
            {osds.map((o) => (
              <tr key={o.id}>
                <td>osd.{o.id}</td>
                <td>{o.host && names.has(o.host) ? <MemberLink name={o.host} /> : o.host || "–"}</td>
                <td>{o.device_class || "–"}</td>
                <td>
                  {o.up ? "up" : <strong role="alert">down</strong>}, {o.in ? "in" : <strong>out</strong>}
                </td>
                {reweighted && <td className="num">{o.weight != null ? o.weight.toFixed(2) : "–"}</td>}
                <td className="num">{o.pgs ?? "–"}</td>
                {s.perf_counters !== false && (
                  <>
                    <td className="num">{o.used != null ? formatBytes(o.used) : "–"}</td>
                    <td className="num">{o.size != null ? formatBytes(o.size) : "–"}</td>
                    <td className="num">
                      {o.size ? <OsdUsed percent={pct(o.used ?? 0, o.size)} nearfull={ratios.nearfull} full={ratios.full} /> : "–"}
                    </td>
                    <td>
                      {o.size ? (
                        // amber from nearfull, red from full
                        <meter
                          min={0}
                          max={100}
                          low={ratios.nearfull * 100}
                          high={ratios.full * 100}
                          optimum={0}
                          value={pct(o.used ?? 0, o.size)}
                          aria-label={`osd.${o.id} used`}
                        />
                      ) : null}
                    </td>
                    <td className="num">
                      <Var osd={o} avg={avg} host={hosts.find((h) => h.host === o.host)} />
                    </td>
                  </>
                )}
                <td className="num">{o.apply_latency_ms != null ? ms(o.apply_latency_ms) : "–"}</td>
                <td className="num">{o.commit_latency_ms != null ? ms(o.commit_latency_ms) : "–"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <HostFill hosts={hosts} replicas={mainReplicas(s.pools ?? [])} />
      <h2 id="pools">Pools</h2>
      <div className="scroll">
        <table>
          <thead>
            <tr>
              <th>Pool</th>
              <th>Type</th>
              <th className="num">Stored</th>
              <th className="num">Available</th>
              <th>Used</th>
              <th className="num">Objects</th>
              <th className="num">PGs</th>
            </tr>
          </thead>
          <tbody>
            {(s.pools ?? []).map((p) => (
              <tr key={p.id}>
                <td>{p.name ?? `pool ${p.id}`}</td>
                <td>
                  {p.type} {p.description && <span className="muted">{p.description}</span>}
                </td>
                <td className="num">{p.stored != null ? formatBytes(p.stored) : "–"}</td>
                <td className="num">{p.max_avail != null ? formatBytes(p.max_avail) : "–"}</td>
                <td>
                  {p.percent_used != null ? (
                    <>
                      <meter min={0} max={100} high={80} value={p.percent_used * 100} aria-label={`${p.name} used`} />{" "}
                      {(p.percent_used * 100).toFixed(1)}%
                    </>
                  ) : (
                    "–"
                  )}
                </td>
                <td className="num">{p.objects != null ? formatNumber(p.objects) : "–"}</td>
                <td className="num">{p.pgs ?? "–"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

interface Host {
  host: string;
  used: number;
  size: number;
  /** null when an OSD on it has no PG count: no even spread can be worked out */
  pgs: number | null;
}

/** An OSD that counts towards averages and hosts, as in ``ceph osd df``: in, with a host,
 * a size and its use (an out OSD's space isn't in CRUSH; D93 review). */
const counted = (o: CephOsd) => o.in !== false && !!o.host && !!o.size && o.used != null;

/** OSDs' use summed by host. */
function hostFill(osds: CephOsd[]): Host[] {
  const by = new Map<string, Host>();
  for (const o of osds.filter(counted)) {
    const h = by.get(o.host!) ?? { host: o.host!, used: 0, size: 0, pgs: 0 };
    h.used += o.used!;
    h.size += o.size!;
    h.pgs = h.pgs != null && o.pgs != null ? h.pgs + o.pgs : null;
    by.set(h.host, h);
  }
  return [...by.values()].sort((a, b) => a.host.localeCompare(b.host));
}

/** The copies of the pool holding the most data ("replica:3" in Ceph's description), so
 * a small pool's count doesn't speak for the cluster; 0 when it isn't replicated. */
function mainReplicas(pools: { description?: string; stored?: number }[]): number {
  const main = [...pools].sort((a, b) => (b.stored ?? 0) - (a.stored ?? 0))[0];
  return Number(/replica:(\d+)/.exec(main?.description ?? "")?.[1] ?? 0);
}

/** ``ceph osd df``'s VAR, flagged from 1.2; the hover says how its PGs (all pools) compare
 * with an even spread by size across its host's OSDs. The upmap balancer allows 5 either
 * way in each pool. */
function Var({ osd, avg, host }: { osd: CephOsd; avg: number; host?: Host }) {
  if (!counted(osd) || !avg) return <>–</>;
  const v = pct(osd.used!, osd.size!) / avg;
  const even = host?.pgs != null && host.size ? Math.round((host.pgs * osd.size!) / host.size) : null;
  const spread = osd.pgs != null && even != null ? `${osd.pgs} PGs; ${even} if ${host!.host}'s were spread by size` : "";
  return (
    <span title={spread || undefined}>
      {v >= 1.2 ? <strong>{v.toFixed(2)}</strong> : v.toFixed(2)}
      {/* the hover's figures and the emphasis, for screen readers too */}
      {(spread || v >= 1.2) && <span className="sr-only">{`${v >= 1.2 ? ", high" : ""}${spread ? `, ${spread}` : ""}`}</span>}
    </span>
  );
}

/** Use by host and, when there are as many hosts as the main pool has copies, why the fullest one sets
 * the limit: each holds a full copy, so a bigger host's extra space can't be used. */
function HostFill({ hosts, replicas }: { hosts: Host[]; replicas: number }) {
  if (hosts.length < 2) return null;
  const smallest = Math.min(...hosts.map((h) => h.size));
  const bigger = hosts.filter((h) => h.size > smallest * 1.1).map((h) => h.host);
  return (
    <>
      <p className="muted" aria-label="Use by host">
        By host:{" "}
        {hosts.map((h, i) => (
          <span key={h.host}>
            {i > 0 && " · "}
            {h.host} {formatPercent(pct(h.used, h.size))} ({formatBytes(h.used)} of {formatBytes(h.size)})
          </span>
        ))}
      </p>
      {replicas === hosts.length && bigger.length > 0 && (
        <p className="muted">
          With {hosts.length} hosts and {replicas} copies, each host holds a full copy (with MicroCeph's default rule,
          which puts copies on different hosts once it has three), so the fullest host sets the limit and the extra space on {bigger.join(", ")} can't be used. Each pool's
          Available, below, is Ceph's estimate of what more fits; see <code>docs/CEPH-METRICS.md</code> on balancing.
        </p>
      )}
    </>
  );
}

/** Each monitor, its host and the free space where it keeps its data (MON_DISK_LOW). */
function Monitors({ mons, members }: { mons: CephMon[]; members: Set<string> }) {
  if (mons.length === 0) return null;
  return (
    <>
      <h2 id="monitors">Monitors</h2>
      <p className="muted">
        Free space on the filesystem holding each monitor's data (<code>/var/snap/microceph/common/data/mon</code>), from
        its host's node_exporter, in whole percent as Ceph counts it. By default Ceph warns at {MON_WARN}% free or less (
        <code>MON_DISK_LOW</code>) and raises an error at {MON_CRIT}% or less (<code>MON_DISK_CRIT</code>).
      </p>
      <div className="scroll">
        <table>
          <thead>
            <tr>
              <th>Monitor</th>
              <th>Host</th>
              <th className="num">Rank</th>
              <th>Quorum</th>
              <th>Data filesystem</th>
              <th className="num">Free</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {mons.map((m) => (
              <tr key={m.name}>
                <td>mon.{m.name}</td>
                <td>
                  {m.member && members.has(m.member) ? <MemberLink name={m.member} /> : m.host || "–"}
                  {m.address && <span className="muted"> {m.address}</span>}
                </td>
                <td className="num">{m.rank ?? "–"}</td>
                <td>{m.in_quorum ? "in" : <strong>out</strong>}</td>
                {m.disk ? (
                  <>
                    <td>
                      <code>{m.disk.mountpoint}</code>{" "}
                      <span className="muted">
                        {formatBytes(m.disk.avail)} free of {formatBytes(m.disk.size)}
                      </span>
                    </td>
                    <td className="num">
                      <MonFree percent={m.disk.free_percent} state={m.disk.state} />
                    </td>
                    <td>
                      {/* whole percent: green above 30% free, amber at 30 or less, red at 5 or less */}
                      <meter
                        min={0}
                        max={100}
                        low={MON_CRIT + 1}
                        high={MON_WARN + 1}
                        optimum={100}
                        value={m.disk.free_percent}
                        aria-label={`mon.${m.name} free`}
                      />
                    </td>
                  </>
                ) : (
                  <td colSpan={3} className="muted" title="No node_exporter data for this host">
                    not monitored
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

/** Not an alert region: the figure changes with every refresh, and the bell already
 * announces MON_DISK_LOW (D92 review). */
function MonFree({ percent, state }: { percent: number; state?: string }) {
  const label = state === "critical" ? "critically low" : state === "low" ? "low" : "";
  return label ? (
    <strong>
      {percent}% {label}
    </strong>
  ) : (
    <>{percent}%</>
  );
}

function History({
  range,
  pools,
  perfCounters,
}: {
  range: number;
  pools: { id: number; name?: string }[];
  perfCounters: boolean;
}) {
  const pool = (id: string) => pools.find((p) => String(p.id) === id)?.name ?? `pool ${id}`;
  const osd = (l: Record<string, string>) => l.ceph_daemon;
  return (
    <div className="grid">
      <MetricPanel
        title="Raw capacity"
        range={range}
        format={formatBytes}
        queries={[
          { name: "ceph_cluster_total_used_raw_bytes", label: () => "used (raw)" },
          { name: "ceph_cluster_total_bytes", label: () => "total (raw)" },
        ]}
      />
      <MetricPanel title="Pool usage" range={range} format={formatBytes} queries={[{ name: "ceph_pool_stored", label: (l) => pool(l.pool_id) }]} />
      <MetricPanel
        title="Pool throughput"
        range={range}
        format={formatRate}
        queries={[
          { name: "ceph_pool_rd_bytes", fn: "rate", label: (l) => `${pool(l.pool_id)} read` },
          { name: "ceph_pool_wr_bytes", fn: "rate", label: (l) => `${pool(l.pool_id)} write` },
        ]}
      />
      <MetricPanel
        title="Pool IOPS"
        range={range}
        format={perSecond}
        queries={[
          { name: "ceph_pool_rd", fn: "rate", label: (l) => `${pool(l.pool_id)} read` },
          { name: "ceph_pool_wr", fn: "rate", label: (l) => `${pool(l.pool_id)} write` },
        ]}
      />
      <MetricPanel title="OSD apply latency" range={range} format={ms} queries={[{ name: "ceph_osd_apply_latency_ms", label: osd }]} />
      <MetricPanel title="OSD commit latency" range={range} format={ms} queries={[{ name: "ceph_osd_commit_latency_ms", label: osd }]} />
      {perfCounters && (
        <>
          <MetricPanel
            title="OSD write latency (per op)"
            range={range}
            format={ms}
            queries={[
              { name: "ceph_osd_op_w_latency_sum", fn: "rate" },
              { name: "ceph_osd_op_w_latency_count", fn: "rate" },
            ]}
            compute={ratioSeries("ceph_daemon", 1000)}
          />
          <MetricPanel
            title="OSD read latency (per op)"
            range={range}
            format={ms}
            queries={[
              { name: "ceph_osd_op_r_latency_sum", fn: "rate" },
              { name: "ceph_osd_op_r_latency_count", fn: "rate" },
            ]}
            compute={ratioSeries("ceph_daemon", 1000)}
          />
        </>
      )}
    </div>
  );
}

/** Ceph Tentacle's mgr leaves OSD perf counters out by default; MicroCeph runs no ceph-exporter. */
function PerfCountersHint() {
  return (
    <p className="muted">
      This Ceph doesn't export OSD performance counters (Ceph Tentacle's default), so OSD used and size and the
      per-operation latency graphs are hidden. To show them, on a member:{" "}
      <code>sudo ceph config set mgr mgr/prometheus/exclude_perf_counters false</code>, then restart the mgr (
      <code>sudo snap restart microceph.mgr</code>); see <code>docs/CEPH-METRICS.md</code>.
    </p>
  );
}

const RBD_METRICS: { metric: RbdMetric; label: string; format: (v: number) => string }[] = [
  { metric: "write_bytes", label: "Write throughput", format: formatRate },
  { metric: "read_bytes", label: "Read throughput", format: formatRate },
  { metric: "write_ops", label: "Write IOPS", format: perSecond },
  { metric: "read_ops", label: "Read IOPS", format: perSecond },
  { metric: "write_latency", label: "Write latency", format: ms },
  { metric: "read_latency", label: "Read latency", format: ms },
];

function Owner({ row }: { row: RbdRow }) {
  const o = row.owner;
  if (o.kind === "instance" && o.name && !o.deleted)
    return (
      <>
        <Link to={instancePath(o.project ?? "default", o.name)}>{o.project === "default" ? o.name : `${o.project}/${o.name}`}</Link>{" "}
        <span className="muted">{o.type === "virtual-machine" ? (o.block ? "VM disk" : "VM config") : "container"}</span>
      </>
    );
  if (o.kind === "volume" && o.name && !o.deleted)
    return (
      <>
        <Link to={volumePath(o.project ?? "default", lxdPool(row.pool), o.name)}>{o.name}</Link> <span className="muted">volume</span>
      </>
    );
  if (o.kind === "image") return <span className="muted">image {o.name?.slice(0, 12)}</span>;
  return <code>{row.image}</code>;
}

// LXD names its Ceph pools after the LXD pool by default (lxd_<pool>); fall back to the Ceph name
const lxdPool = (cephPool: string) => cephPool.replace(/^lxd_/, "");

function Rbd({ range }: { range: number }) {
  const [metric, setMetric] = useState<RbdMetric>("write_bytes");
  const { data, error } = useApi(`rbd:${metric}:${range}`, () => fetchRbdTop(metric, Math.min(range, 31 * 86400)), 60_000);
  const m = RBD_METRICS.find((x) => x.metric === metric)!;
  return (
    <section aria-label="Busiest disks">
      <h2>Busiest instance disks and volumes</h2>
      <p className="muted">Per RBD image, averaged over the range (needs <code>rbd_stats_pools</code>, see docs/CEPH-METRICS.md).</p>
      <label className="field">
        Show
        <select value={metric} onChange={(e) => setMetric(e.target.value as RbdMetric)}>
          {RBD_METRICS.map((x) => (
            <option key={x.metric} value={x.metric}>
              {x.label}
            </option>
          ))}
        </select>
      </label>
      {error && <p role="alert">{error}</p>}
      {data && data.rows.length === 0 && <p>No RBD image data in this range.</p>}
      {data && data.rows.length > 0 && (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>Disk</th>
                <th>Ceph pool</th>
                <th className="num">{m.label}</th>
              </tr>
            </thead>
            <tbody>
              {data.rows.map((r) => (
                <tr key={`${r.pool}/${r.image}`}>
                  <td>
                    <Owner row={r} />
                  </td>
                  <td>{r.pool}</td>
                  <td className="num">{m.format(r.value)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
