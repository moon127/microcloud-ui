import type { Labels, QueryResult } from "../../api";
import MetricPanel from "../../chart/MetricPanel";
import type { PanelQuery } from "../../chart/panel";
import { ratioPercent, subtract } from "../../chart/series";
import { formatBytes, formatNumber, formatPercent, formatRate } from "../../format";
import { useRange } from "../../range";
import RangePicker from "../../RangePicker";

const perSecond = (v: number) => `${formatNumber(v)}/s`;
const fixed = (label: string) => () => label;

export default function Graphs({ project, name }: { project: string; name: string }) {
  const range = useRange();
  const match: Labels = { project, name };
  const q = (metric: string, extra: Omit<PanelQuery, "name"> = {}): PanelQuery => ({
    name: metric,
    match,
    ...extra,
  });
  const dev = (suffix: string) => (l: Labels) => `${l.device} ${suffix}`;

  return (
    <>
      <RangePicker />
      <div className="grid">
        <MetricPanel
          title="CPU (% of its CPUs)"
          range={range}
          format={formatPercent}
          queries={[
            q("mcui_instance_cpu_busy_seconds_total", { fn: "rate", by: ["name"] }),
            q("mcui_instance_cpus", { by: ["name"] }),
          ]}
          compute={([busy, cpus]: QueryResult[]) =>
            busy.series.map((s, i) => ({ label: "CPU", points: ratioPercent(s.points, cpus.series[i]?.points ?? []) }))
          }
        />
        <MetricPanel
          title="Memory"
          range={range}
          format={formatBytes}
          queries={[
            q("mcui_instance_memory_used_bytes", { by: ["name"], label: fixed("used") }),
            q("lxd_memory_RSS_bytes", { by: ["name"], label: fixed("RSS") }),
            q("lxd_memory_Cached_bytes", { by: ["name"], label: fixed("cache") }),
            q("lxd_memory_Swap_bytes", { by: ["name"], label: fixed("swap") }),
            q("lxd_memory_MemTotal_bytes", { by: ["name"], label: fixed("limit") }),
          ]}
        />
        <MetricPanel
          title="Disk throughput"
          range={range}
          format={formatRate}
          queries={[
            q("lxd_disk_read_bytes_total", { fn: "rate", label: dev("read") }),
            q("lxd_disk_written_bytes_total", { fn: "rate", label: dev("write") }),
          ]}
        />
        <MetricPanel
          title="Disk IOPS"
          range={range}
          format={perSecond}
          queries={[
            q("lxd_disk_reads_completed_total", { fn: "rate", label: dev("reads") }),
            q("lxd_disk_writes_completed_total", { fn: "rate", label: dev("writes") }),
          ]}
        />
        <MetricPanel
          title="Network throughput"
          range={range}
          format={formatRate}
          queries={[
            q("lxd_network_receive_bytes_total", { fn: "rate", label: dev("rx") }),
            q("lxd_network_transmit_bytes_total", { fn: "rate", label: dev("tx") }),
          ]}
        />
        <MetricPanel
          title="Network packets"
          range={range}
          format={perSecond}
          queries={[
            q("lxd_network_receive_packets_total", { fn: "rate", label: dev("rx") }),
            q("lxd_network_transmit_packets_total", { fn: "rate", label: dev("tx") }),
          ]}
        />
        <MetricPanel
          title="Network errors and drops"
          range={range}
          format={perSecond}
          queries={[
            q("lxd_network_receive_errs_total", { fn: "rate", label: dev("rx errors") }),
            q("lxd_network_transmit_errs_total", { fn: "rate", label: dev("tx errors") }),
            q("lxd_network_receive_drop_total", { fn: "rate", label: dev("rx drops") }),
            q("lxd_network_transmit_drop_total", { fn: "rate", label: dev("tx drops") }),
          ]}
        />
        <MetricPanel
          title="Filesystem used"
          range={range}
          format={formatBytes}
          queries={[q("lxd_filesystem_size_bytes"), q("lxd_filesystem_avail_bytes")]}
          compute={([size, avail]: QueryResult[]) =>
            size.series.map((s) => {
              const a = avail.series.find((x) => x.labels.mountpoint === s.labels.mountpoint && x.labels.device === s.labels.device);
              return { label: s.labels.mountpoint, points: subtract(s.points, a?.points ?? []) };
            })
          }
        />
        <MetricPanel
          title="Processes"
          range={range}
          format={formatNumber}
          queries={[q("lxd_procs_total", { by: ["name"], label: fixed("processes") })]}
        />
        <MetricPanel
          title="OOM kills (total)"
          range={range}
          format={formatNumber}
          queries={[q("lxd_memory_OOM_kills_total", { by: ["name"], label: fixed("OOM kills") })]}
        />
      </div>
    </>
  );
}
