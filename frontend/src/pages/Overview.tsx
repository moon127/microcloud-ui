import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router";
import {
  ApiError,
  fetchLiveMetrics,
  fetchServer,
  fetchTop,
  type InstanceLive,
  type LiveMetrics,
  type MemberLive,
  type ServerInfo,
  type TopMetric,
  type TopResult,
} from "../api";
import { formatBytes, formatDuration, formatPercent, formatRate } from "../format";
import { useRange } from "../range";
import RangePicker from "../RangePicker";
import { useSession } from "../session";
import { useApi } from "../useApi";
import { InstanceLink } from "../links";

const DEFAULT_INTERVAL_S = 15;

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export default function Overview() {
  const { onExpired } = useSession();
  const [server, setServer] = useState<ServerInfo | null>(null);
  const [live, setLive] = useState<LiveMetrics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;

    function failed(e: unknown): boolean {
      if (e instanceof ApiError && e.status === 401) {
        onExpired();
        return true;
      }
      setError(message(e));
      return false;
    }

    fetchServer()
      .then((s) => active && setServer(s))
      .catch((e: unknown) => active && failed(e));

    async function tick() {
      let delay = DEFAULT_INTERVAL_S * 1000;
      try {
        const m = await fetchLiveMetrics();
        if (!active) return;
        setLive(m);
        setError(null);
        delay = m.interval_seconds * 1000;
      } catch (e) {
        if (!active || failed(e)) return;
      }
      timer = setTimeout(tick, delay);
    }
    void tick();

    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [onExpired]);

  const instances = useMemo(() => {
    const q = filter.trim().toLowerCase();
    const rows = (live?.instances ?? []).filter(
      (i) => !q || `${i.name} ${i.project} ${i.member}`.toLowerCase().includes(q),
    );
    return rows.sort((a, b) => (b.cpu_percent ?? -1) - (a.cpu_percent ?? -1) || a.name.localeCompare(b.name));
  }, [live, filter]);

  return (
    <>
      {error && <p role="alert">{error}</p>}

      <section aria-label="Server">
        {server ? (
          <p>
            LXD {server.version} · {server.clustered ? "cluster" : "standalone"} · via {server.endpoint}
            {Object.entries(server.features).some(([, on]) => !on) && (
              <>
                {" "}
                · unavailable:{" "}
                {Object.entries(server.features)
                  .filter(([, on]) => !on)
                  .map(([k]) => k)
                  .join(", ")}
              </>
            )}
          </p>
        ) : (
          <p role="status">Connecting to LXD…</p>
        )}
      </section>

      <section aria-label="Members">
        <h2>Members</h2>
        {live ? <MembersTable members={live.members} /> : <p role="status">Loading metrics…</p>}
      </section>

      <TopSection />

      <section aria-label="Instances">
        <h2>Instances (live)</h2>
        <input
          type="search"
          placeholder="Filter by name, project or member"
          aria-label="Filter instances"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
        {live && <InstancesTable rows={instances} />}
        {live?.scraped_at != null && (
          <p className="muted">
            Updated {new Date(live.scraped_at * 1000).toLocaleTimeString()} · every {live.interval_seconds}s. Rates
            appear after the second sample.
          </p>
        )}
      </section>
    </>
  );
}

const TOP: { metric: TopMetric; title: string; format: (v: number) => string }[] = [
  { metric: "cpu", title: "CPU", format: formatPercent },
  { metric: "memory", title: "Memory", format: formatBytes },
  { metric: "net", title: "Network", format: formatRate },
  { metric: "disk", title: "Disk I/O", format: formatRate },
];

function TopSection() {
  const range = useRange();
  return (
    <section aria-label="Top instances">
      <h2>Top instances</h2>
      <RangePicker />
      <div className="grid">
        {TOP.map((t) => (
          <TopTable key={t.metric} {...t} range={range} />
        ))}
      </div>
    </section>
  );
}

function TopTable({ metric, title, format, range }: (typeof TOP)[number] & { range: number }) {
  const { data, error } = useApi<TopResult>(`top:${metric}:${range}`, () => fetchTop(metric, range, 5), 60_000);
  return (
    <figure className="chart" aria-label={`Top ${title}`}>
      <figcaption>{title} (average)</figcaption>
      {error && <p role="alert">{error}</p>}
      {data && data.rows.length === 0 && <p className="muted">No data yet.</p>}
      {data && data.rows.length > 0 && (
        <table>
          <tbody>
            {data.rows.map((r) => (
              <tr key={`${r.project}/${r.name}`}>
                <td>
                  <InstanceLink project={r.project} name={r.name} />
                </td>
                <td className="num">{format(r.value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </figure>
  );
}

function MembersTable({ members }: { members: MemberLive[] }) {
  return (
    <div className="scroll">
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Status</th>
            <th>Roles</th>
            <th>Metrics</th>
            <th>Instances</th>
            <th>LXD uptime</th>
            <th>Goroutines</th>
            <th>Warnings</th>
          </tr>
        </thead>
        <tbody>
          {members.map((m) => (
            <tr key={m.name}>
              <td>
                <Link to={`/members/${encodeURIComponent(m.name)}`}>{m.name}</Link>
              </td>
              <td>{m.status}</td>
              <td>{m.roles.join(", ")}</td>
              <td>
                {m.scrape.ok
                  ? `ok (${m.scrape.port}${m.scrape.fell_back ? ", fallback" : ""})`
                  : `error: ${m.scrape.error}`}
              </td>
              <td>{m.instances}</td>
              <td>{formatDuration(m.daemon.uptime_seconds)}</td>
              <td>{m.daemon.goroutines ?? "–"}</td>
              <td>{m.daemon.warnings ?? "–"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function memory(i: InstanceLive): string {
  if (i.memory_used == null) return "–";
  return i.memory_total ? `${formatBytes(i.memory_used)} / ${formatBytes(i.memory_total)}` : formatBytes(i.memory_used);
}

function InstancesTable({ rows }: { rows: InstanceLive[] }) {
  if (rows.length === 0) return <p>No instances.</p>;
  return (
    <div className="scroll">
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Project</th>
            <th>Member</th>
            <th>Type</th>
            <th>CPU</th>
            <th>Memory</th>
            <th>Root FS</th>
            <th>Disk read</th>
            <th>Disk write</th>
            <th>Net rx</th>
            <th>Net tx</th>
            <th>Procs</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((i) => (
            <tr key={`${i.member}/${i.project}/${i.name}`}>
              <td>
                <InstanceLink project={i.project} name={i.name} />
              </td>
              <td>{i.project}</td>
              <td>{i.member}</td>
              <td>{i.type === "virtual-machine" ? "VM" : "container"}</td>
              <td className="num">{formatPercent(i.cpu_percent)}</td>
              <td className="num">{memory(i)}</td>
              <td className="num">
                {i.rootfs_used == null ? "–" : `${formatBytes(i.rootfs_used)} / ${formatBytes(i.rootfs_size)}`}
              </td>
              <td className="num">{formatRate(i.disk_read_bps)}</td>
              <td className="num">{formatRate(i.disk_write_bps)}</td>
              <td className="num">{formatRate(i.net_rx_bps)}</td>
              <td className="num">{formatRate(i.net_tx_bps)}</td>
              <td className="num">{i.procs ?? "–"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
