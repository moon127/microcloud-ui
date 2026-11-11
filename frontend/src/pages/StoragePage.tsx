import { Link } from "react-router";
import { fetchStoragePools, queryMetric } from "../api";
import { useActions } from "../actions";
import MetricPanel from "../chart/MetricPanel";
import { poolSeries, refreshFor } from "../chart/panel";
import { formatBytes, formatNumber, formatRate } from "../format";
import { useRange } from "../range";
import RangePicker from "../RangePicker";
import { useSession } from "../session";
import { useApi } from "../useApi";
import { poolPath } from "./storage/paths";

const perSecond = (v: number) => `${formatNumber(v)}/s`;

export default function StoragePage() {
  const range = useRange();
  const pools = useApi("pool-series", () => queryMetric({ name: "mcui_pool_space_total", by: ["pool"], range: 900 }));
  const ceph = useApi("ceph-pools", () => queryMetric({ name: "ceph_pool_metadata", range: 900 }), refreshFor(range));
  const poolNames = (pools.data?.series ?? []).map((s) => s.labels.pool);
  const cephPools = (ceph.data?.series ?? []).map((s) => ({ id: s.labels.pool_id, name: s.labels.name }));

  return (
    <>
      <h1>Storage</h1>
      <PoolList />
      <h2>Capacity history</h2>
      {pools.error && <p role="alert">{pools.error}</p>}
      <RangePicker />
      <h3>LXD storage pools (per member)</h3>
      {pools.data && poolNames.length === 0 && <p className="muted">No pool data collected yet.</p>}
      <div className="grid">
        {poolNames.map((pool) => (
          <MetricPanel
            key={pool}
            title={`${pool}: space used`}
            range={range}
            format={formatBytes}
            queries={[
              { name: "mcui_pool_space_used", match: { pool } },
              { name: "mcui_pool_space_total", match: { pool } },
            ]}
            compute={poolSeries}
          />
        ))}
      </div>
      {cephPools.length > 0 && (
        <>
          <h3>Ceph pools</h3>
          <div className="grid">
            <MetricPanel
              title="Ceph pool usage"
              range={range}
              format={formatBytes}
              queries={[{ name: "ceph_pool_stored", label: (l) => poolLabel(cephPools, l.pool_id) }]}
            />
            <MetricPanel
              title="Ceph pool throughput"
              range={range}
              format={formatRate}
              queries={[
                { name: "ceph_pool_rd_bytes", fn: "rate", label: (l) => `${poolLabel(cephPools, l.pool_id)} read` },
                { name: "ceph_pool_wr_bytes", fn: "rate", label: (l) => `${poolLabel(cephPools, l.pool_id)} write` },
              ]}
            />
            <MetricPanel
              title="Ceph pool IOPS"
              range={range}
              format={perSecond}
              queries={[
                { name: "ceph_pool_rd", fn: "rate", label: (l) => `${poolLabel(cephPools, l.pool_id)} read` },
                { name: "ceph_pool_wr", fn: "rate", label: (l) => `${poolLabel(cephPools, l.pool_id)} write` },
              ]}
            />
            <MetricPanel
              title="Ceph raw capacity"
              range={range}
              format={formatBytes}
              queries={[
                { name: "ceph_cluster_total_used_raw_bytes", label: () => "used (raw)" },
                { name: "ceph_cluster_total_bytes", label: () => "total (raw)" },
              ]}
            />
          </div>
        </>
      )}
      <p className="muted">
        Ceph OSD latency, PG states and per-disk (RBD) I/O come with the Ceph dashboard in Phase 4.
      </p>
    </>
  );
}

function poolLabel(pools: { id: string; name: string }[], id: string): string {
  return pools.find((p) => p.id === id)?.name ?? `pool ${id}`;
}


function PoolList() {
  const { user } = useSession();
  const { version } = useActions();
  const { data, error } = useApi(`pools:${version}`, fetchStoragePools, 60_000);
  return (
    <>
      <h2>LXD storage pools</h2>
      <p className="actions">
        {user.role === "admin" && (
          <Link className="button primary" to="/storage/pools/new">
            Create pool
          </Link>
        )}
        <Link to="/storage/volumes">Custom volumes</Link>
      </p>
      {error && <p role="alert">{error}</p>}
      {data && data.length === 0 && <p>No storage pools.</p>}
      {data && data.length > 0 && (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Driver</th>
                <th>Status</th>
                <th>Source</th>
                <th>Used by</th>
                <th>Description</th>
              </tr>
            </thead>
            <tbody>
              {data.map((p) => (
                <tr key={p.name}>
                  <td>
                    <Link to={poolPath(p.name)}>{p.name}</Link>
                  </td>
                  <td>{p.driver}</td>
                  <td>{p.status}</td>
                  <td>{p.config?.source || p.config?.["ceph.osd.pool_name"] || "–"}</td>
                  <td className="num">{p.used_by ?? 0}</td>
                  <td>{p.description}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
