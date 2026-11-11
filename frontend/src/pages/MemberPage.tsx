import { useParams } from "react-router";
import { useState } from "react";
import {
  evacuateMember,
  fetchHosts,
  fetchMemberResources,
  fetchMembers,
  restoreMember,
  type EvacuationMode,
  type Labels,
  type QueryResult,
  type Resources,
} from "../api";
import { useActions } from "../actions";
import Confirm from "../Confirm";
import { useSession } from "../session";
import MetricPanel from "../chart/MetricPanel";
import { cpuModeSeries, nonZero, usedPercentSeries, type PanelQuery } from "../chart/panel";
import { add, subtract } from "../chart/series";
import { formatBytes, formatNumber, formatPercent, formatRate } from "../format";
import { HOST_METRICS_DOC } from "./hostValues";
import { useRange } from "../range";
import RangePicker from "../RangePicker";
import { useApi } from "../useApi";

const fixed = (label: string) => () => label;
const perSecond = (v: number) => `${formatNumber(v)}/s`;
const cores = (v: number) => `${formatNumber(v)} CPUs`;

export default function MemberPage() {
  const { name = "" } = useParams();
  const range = useRange();
  const res = useApi(`resources:${name}`, () => fetchMemberResources(name));
  const hosts = useApi("hosts", fetchHosts);
  const node = hosts.data?.hosts.find((h) => h.name === name)?.node ?? null;
  const m: Labels = { member: name };
  const q = (metric: string, extra: Omit<PanelQuery, "name"> = {}): PanelQuery => ({ name: metric, match: m, ...extra });
  const net = [
    q("mcui_host_network_receive_bytes_total", { fn: "rate", label: (l) => `${l.network} rx` }),
    q("mcui_host_network_transmit_bytes_total", { fn: "rate", label: (l) => `${l.network} tx` }),
  ];

  return (
    <>
      <h1>{name}</h1>
      <MemberActions name={name} />
      {res.error && <p role="alert">{res.error}</p>}
      {res.data && <Hardware r={res.data} />}
      <RangePicker />
      {node?.ok && <NodePanels name={name} range={range} />}
      {node && !node.ok && (
        <p className={node.absent ? "muted" : undefined} role={node.absent ? undefined : "alert"}>
          {node.absent
            ? `No node_exporter on this host: host CPU %, disk and pressure graphs need it (${HOST_METRICS_DOC}).`
            : `node_exporter: ${node.error}`}
        </p>
      )}
      {node?.ok && <h2>LXD</h2>}
      <div className="grid">
        <MetricPanel
          title="Load average"
          range={range}
          format={formatNumber}
          queries={[
            q("mcui_member_load1", { label: fixed("1 min") }),
            q("mcui_member_load5", { label: fixed("5 min") }),
            q("mcui_member_load15", { label: fixed("15 min") }),
          ]}
        />
        {!node?.ok && (
          <MetricPanel
            title="Host memory"
            range={range}
            format={formatBytes}
            queries={[
              q("mcui_member_memory_total_bytes"),
              q("mcui_member_memory_free_bytes"),
              q("mcui_member_memory_buffered_bytes"),
            ]}
            compute={([total, free, buffered]: QueryResult[]) => {
              const t = total.series[0]?.points ?? [];
              const used = subtract(subtract(t, free.series[0]?.points ?? []), buffered.series[0]?.points ?? []);
              return [
                { label: "used (excl. buffers)", points: used },
                { label: "total", points: t },
              ];
            }}
          />
        )}
        <MetricPanel
          title="Instances' CPU use"
          range={range}
          format={cores}
          queries={[
            q("mcui_instance_cpu_busy_seconds_total", { fn: "rate", by: ["member"], label: fixed("instances") }),
            q("mcui_member_cpus", { label: fixed("host CPUs") }),
          ]}
        />
        <MetricPanel
          title="Instances' memory use"
          range={range}
          format={formatBytes}
          queries={[q("mcui_instance_memory_used_bytes", { by: ["member"], label: fixed("instances") })]}
        />
        <MetricPanel title="Host network" range={range} format={formatRate} queries={net} compute={nonZero(net)} />
        <MetricPanel
          title="LXD API requests"
          range={range}
          format={perSecond}
          queries={[q("lxd_api_requests_completed_total", { fn: "rate", by: ["result"], label: (l) => l.result })]}
        />
        <MetricPanel
          title="LXD daemon"
          range={range}
          format={formatNumber}
          queries={[
            q("lxd_go_goroutines", { label: fixed("goroutines") }),
            q("lxd_api_requests_ongoing", { by: ["member"], label: fixed("ongoing requests") }),
            q("lxd_warnings_total", { label: fixed("warnings") }),
          ]}
        />
      </div>
    </>
  );
}

const pctFmt = (v: number) => formatPercent(v);

/** Host graphs from node_exporter (MO-12, D58). */
function NodePanels({ name, range }: { name: string; range: number }) {
  const m: Labels = { member: name };
  const q = (metric: string, extra: Omit<PanelQuery, "name"> = {}): PanelQuery => ({
    name: metric,
    match: m,
    ...extra,
  });
  const disk = [
    q("node_disk_read_bytes_total", {
      fn: "rate",
      label: (l) => `${l.device} read`,
    }),
    q("node_disk_written_bytes_total", {
      fn: "rate",
      label: (l) => `${l.device} write`,
    }),
  ];
  return (
    <>
      <h2>Host (node_exporter)</h2>
      <div className="grid">
        <MetricPanel
          title="Host CPU"
          range={range}
          format={pctFmt}
          queries={[q("node_cpu_seconds_total", { fn: "rate", by: ["mode"] })]}
          compute={cpuModeSeries}
        />
        <MetricPanel
          title="Host memory"
          range={range}
          format={formatBytes}
          queries={[
            q("node_memory_MemTotal_bytes"),
            q("node_memory_MemAvailable_bytes"),
            q("node_memory_Cached_bytes"),
            q("node_memory_Buffers_bytes"),
            q("node_memory_SwapTotal_bytes"),
            q("node_memory_SwapFree_bytes"),
            q("node_zfs_arc_size"),
          ]}
          compute={([total, avail, cached, buffers, swapTotal, swapFree, arc]: QueryResult[]) => {
            const p = (r: QueryResult) => r.series[0]?.points ?? [];
            const out = [
              { label: "used", points: subtract(p(total), p(avail)) },
              { label: "cache + buffers", points: add(p(cached), p(buffers)) },
              { label: "total", points: p(total) },
            ];
            if (p(swapTotal).some(([, v]) => v > 0))
              out.push({
                label: "swap used",
                points: subtract(p(swapTotal), p(swapFree)),
              });
            // the zfs module reports a few bytes of ARC when no pool is imported
            if (p(arc).some(([, v]) => v >= 1024 ** 2)) out.push({ label: "ZFS ARC", points: p(arc) });
            return out;
          }}
        />
        <MetricPanel
          title="Filesystems used"
          range={range}
          format={pctFmt}
          queries={[q("node_filesystem_size_bytes"), q("node_filesystem_avail_bytes")]}
          compute={usedPercentSeries("mountpoint")}
        />
        <MetricPanel title="Host disk I/O" range={range} format={formatRate} queries={disk} compute={nonZero(disk)} />
        <MetricPanel
          title="Disk busy"
          range={range}
          format={pctFmt}
          queries={[
            q("node_disk_io_time_seconds_total", {
              fn: "rate",
              label: (l) => l.device,
            }),
          ]}
          compute={([busy]: QueryResult[]) =>
            busy.series.map((s) => ({
              label: s.labels.device,
              points: s.points.map(([t, v]) => [t, v * 100] as [number, number]),
            }))
          }
        />
        <MetricPanel
          title="Pressure (time waiting)"
          range={range}
          format={pctFmt}
          queries={[
            q("node_pressure_cpu_waiting_seconds_total", { fn: "rate" }),
            q("node_pressure_memory_waiting_seconds_total", { fn: "rate" }),
            q("node_pressure_io_waiting_seconds_total", { fn: "rate" }),
          ]}
          compute={(rs: QueryResult[]) =>
            ["cpu", "memory", "io"].map((label, i) => ({
              label,
              points: (rs[i].series[0]?.points ?? []).map(([t, v]) => [t, v * 100] as [number, number]),
            }))
          }
        />
      </div>
    </>
  );
}

function threads(r: Resources): number {
  return r.cpu.total;
}

function Hardware({ r }: { r: Resources }) {
  const sockets = r.cpu.sockets ?? [];
  const nics = r.network?.cards ?? [];
  const disks = r.storage?.disks ?? [];
  const gpus = r.gpu?.cards ?? [];
  return (
    <section aria-label="Hardware" className="hardware">
      <h2>Hardware</h2>
      <dl>
        {r.system?.product && (
          <>
            <dt>System</dt>
            <dd>
              {r.system.vendor} {r.system.product}
            </dd>
          </>
        )}
        <dt>CPU</dt>
        <dd>
          {sockets.map((s) => s.name).filter(Boolean).join(", ") || r.cpu.architecture || "–"} ({sockets.length || 1}{" "}
          socket{sockets.length > 1 ? "s" : ""}, {threads(r)} threads)
        </dd>
        <dt>Memory</dt>
        <dd>{formatBytes(r.memory.total)}</dd>
        <dt>GPUs</dt>
        <dd>{gpus.length ? gpus.map((g) => [g.vendor, g.product].filter(Boolean).join(" ")).join("; ") : "none"}</dd>
        <dt>NICs</dt>
        <dd>
          {nics.length
            ? nics
                .map((c) => {
                  const ports = (c.ports ?? [])
                    .map((p) => `${p.id}${p.link_detected ? ` ${p.link_speed ? `${p.link_speed / 1000}G` : "up"}` : " down"}`)
                    .join(", ");
                  return `${c.product ?? c.driver ?? "NIC"}${ports ? ` (${ports})` : ""}`;
                })
                .join("; ")
            : "none"}
        </dd>
        <dt>Disks</dt>
        <dd>
          {disks.length
            ? disks.map((d) => `${d.id}: ${d.model ?? "disk"}${d.type ? ` ${d.type}` : ""} ${formatBytes(d.size)}`).join("; ")
            : "none"}
        </dd>
      </dl>
    </section>
  );
}

const MODES: { value: EvacuationMode | ""; label: string }[] = [
  { value: "", label: "Default (per instance cluster.evacuate setting)" },
  { value: "auto", label: "auto: migrate where possible, else stop" },
  { value: "migrate", label: "migrate (cold)" },
  { value: "live-migrate", label: "live-migrate (VMs)" },
  { value: "stop", label: "stop instances, don't move them" },
];

function MemberActions({ name }: { name: string }) {
  const { user } = useSession();
  const { run, version } = useActions();
  const members = useApi(`members:${version}`, fetchMembers);
  const [confirm, setConfirm] = useState<"evacuate" | "restore" | null>(null);
  const [mode, setMode] = useState<EvacuationMode | "">("");
  const member = members.data?.find((m) => m.name === name);
  if (!member) return null;
  const admin = user.role === "admin";
  return (
    <p>
      Status: <strong>{member.status}</strong>
      {member.message ? ` (${member.message})` : ""} · roles: {member.roles.join(", ") || "none"}
      {admin && (
        <>
          {" "}
          {member.status === "Evacuated" ? (
            <button type="button" onClick={() => setConfirm("restore")}>
              Restore
            </button>
          ) : (
            <button type="button" className="danger" onClick={() => setConfirm("evacuate")}>
              Evacuate
            </button>
          )}
        </>
      )}
      {confirm === "evacuate" && (
        <Confirm
          title={`Evacuate ${name}?`}
          confirmLabel="Evacuate"
          danger
          onCancel={() => setConfirm(null)}
          onConfirm={() => {
            setConfirm(null);
            void run(`Evacuate ${name}`, () => evacuateMember(name, mode || undefined));
          }}
        >
          <p>All instances on this member are moved or stopped, and no new ones are placed here until it is restored.</p>
          <label>
            Mode{" "}
            <select value={mode} onChange={(e) => setMode(e.target.value as EvacuationMode | "")}>
              {MODES.map((m) => (
                <option key={m.value} value={m.value}>
                  {m.label}
                </option>
              ))}
            </select>
          </label>
        </Confirm>
      )}
      {confirm === "restore" && (
        <Confirm
          title={`Restore ${name}?`}
          confirmLabel="Restore"
          onCancel={() => setConfirm(null)}
          onConfirm={() => {
            setConfirm(null);
            void run(`Restore ${name}`, () => restoreMember(name));
          }}
        >
          <p>Instances evacuated from this member are moved back and started.</p>
        </Confirm>
      )}
    </p>
  );
}
