import { Link } from "react-router";
import { fetchStoragePools, queryMetric } from "../api";
import { useActions } from "../actions";
import MetricPanel from "../chart/MetricPanel";
import { poolSeries } from "../chart/panel";
import { formatBytes } from "../format";
import { useRange } from "../range";
import RangePicker from "../RangePicker";
import { useSession } from "../session";
import { useApi } from "../useApi";
import { poolPath } from "./storage/paths";

export default function StoragePage() {
  const range = useRange();
  const pools = useApi("pool-series", () => queryMetric({ name: "mcui_pool_space_total", by: ["pool"], range: 900 }));
  const poolNames = (pools.data?.series ?? []).map((s) => s.labels.pool);

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
      <p className="muted">
        Ceph health, OSDs, pools, latency and per-disk I/O: <Link to="/ceph">Ceph</Link>.
      </p>
    </>
  );
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
                <th className="num">Used by</th>
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
