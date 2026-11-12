import { useState } from "react";
import { Link } from "react-router";
import { fetchCeph, fetchMembers, fetchRbdTop, type CephState, type RbdMetric, type RbdRow } from "../api";
import MetricPanel from "../chart/MetricPanel";
import { ratioSeries } from "../chart/panel";
import { formatBytes, formatNumber, formatRate } from "../format";
import { MemberLink } from "../links";
import { instancePath } from "../paths";
import { useRange } from "../range";
import RangePicker from "../RangePicker";
import { useApi } from "../useApi";
import { volumePath } from "./storage/paths";

const perSecond = (v: number) => `${formatNumber(v)}/s`;
const ms = (v: number) => `${v.toFixed(v < 10 ? 1 : 0)} ms`;
const pct = (used: number, total: number) => (total ? (used / total) * 100 : 0);
const count = (v: number) => Math.round(v).toLocaleString("en");

export default function CephPage() {
  const range = useRange();
  const { data, error } = useApi("ceph", fetchCeph, 15_000);
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
          <History range={range} pools={data.pools ?? []} />
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
  const states = Object.entries(s.pgs?.states ?? {});
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
      <h2>OSDs</h2>
      <div className="scroll">
        <table>
          <thead>
            <tr>
              <th>OSD</th>
              <th>Host</th>
              <th>Class</th>
              <th>State</th>
              <th className="num">PGs</th>
              <th className="num">Used</th>
              <th className="num">Size</th>
              <th />
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
                <td className="num">{o.pgs ?? "–"}</td>
                <td className="num">{o.used != null ? formatBytes(o.used) : "–"}</td>
                <td className="num">{o.size != null ? formatBytes(o.size) : "–"}</td>
                <td>{o.size ? <meter min={0} max={100} high={80} value={pct(o.used ?? 0, o.size)} aria-label={`osd.${o.id} used`} /> : null}</td>
                <td className="num">{o.apply_latency_ms != null ? ms(o.apply_latency_ms) : "–"}</td>
                <td className="num">{o.commit_latency_ms != null ? ms(o.commit_latency_ms) : "–"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h2>Pools</h2>
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

function History({ range, pools }: { range: number; pools: { id: number; name?: string }[] }) {
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
    </div>
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
