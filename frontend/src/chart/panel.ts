import type { Labels, MetricQuery, QueryResult } from "../api";
import { combine, type ChartSeries } from "./series";

export interface PanelQuery extends Omit<MetricQuery, "range"> {
  /** Legend label for each returned series. */
  label?: (labels: Labels) => string;
}

export function defaultSeries(queries: PanelQuery[], results: QueryResult[]): ChartSeries[] {
  return results.flatMap((r, i) =>
    r.series.map((s) => ({
      label: queries[i].label ? queries[i].label(s.labels) : queries[i].name,
      points: s.points,
    })),
  );
}

/** Drop series that are zero throughout (e.g. unplugged NICs). */
export function nonZero(queries: PanelQuery[]) {
  return (results: QueryResult[]) =>
    defaultSeries(queries, results).filter((s) => s.points.some(([, v]) => v !== 0));
}

/** Refresh short ranges every minute; long ones don't change noticeably. */
export const refreshFor = (range: number) => (range <= 21600 ? 60_000 : undefined);

/** Used space per member; size once if all members agree (shared pools such as Ceph). */
export function poolSeries([used, total]: QueryResult[]): ChartSeries[] {
  const out: ChartSeries[] = used.series.map((s) => ({ label: `${s.labels.member} used`, points: s.points }));
  const lastValues = new Set(total.series.map((s) => s.points.at(-1)?.[1]));
  if (total.series.length > 0 && lastValues.size === 1) {
    out.push({ label: "size", points: total.series[0].points });
  } else {
    out.push(...total.series.map((s) => ({ label: `${s.labels.member} size`, points: s.points })));
  }
  return out;
}

/** ``sum``/``count`` counter rates per series (mean latency), times ``scale``; label from ``key``. */
export function ratioSeries(key: string, scale: number, suffix = "") {
  return ([sum, count]: QueryResult[]): ChartSeries[] =>
    sum.series.flatMap((s) => {
      const c = count.series.find((x) => x.labels[key] === s.labels[key]);
      if (!c) return [];
      const byT = new Map(c.points.map(([t, v]) => [t, v]));
      const points = s.points.flatMap(([t, v]): [number, number][] => {
        const n = byT.get(t);
        return n ? [[t, (v / n) * scale]] : [];
      });
      return [{ label: `${s.labels[key]}${suffix}`, points }];
    });
}

const CPU_GROUPS: Record<string, string> = {
  user: "user",
  system: "system",
  iowait: "iowait",
  steal: "steal",
  idle: "",
};

/** node_cpu_seconds_total rates by mode as % of all CPU time: user, system, iowait,
 * steal and everything else (nice, irq, softirq) as "other"; idle is left out. */
export function cpuModeSeries([modes]: QueryResult[]): ChartSeries[] {
  const total = new Map<number, number>();
  for (const s of modes.series) for (const [t, v] of s.points) total.set(t, (total.get(t) ?? 0) + v);
  const groups = new Map<string, Map<number, number>>();
  for (const s of modes.series) {
    const g = CPU_GROUPS[s.labels.mode] ?? "other";
    if (!g) continue;
    const m = groups.get(g) ?? new Map<number, number>();
    for (const [t, v] of s.points) m.set(t, (m.get(t) ?? 0) + v);
    groups.set(g, m);
  }
  return ["user", "system", "iowait", "steal", "other"].flatMap((g) => {
    const m = groups.get(g);
    if (!m) return [];
    const points = [...m].flatMap(([t, v]): [number, number][] => {
      const all = total.get(t);
      return all ? [[t, (v / all) * 100]] : [];
    });
    return [{ label: g, points: points.sort((a, b) => a[0] - b[0]) }];
  });
}

/** (size - avail) / size per ``key`` label value, in %. */
export function usedPercentSeries(key: string) {
  return ([size, avail]: QueryResult[]): ChartSeries[] =>
    size.series.flatMap((s) => {
      const a = avail.series.find((x) => x.labels[key] === s.labels[key]);
      return a
        ? [
            {
              label: s.labels[key],
              points: combine(s.points, a.points, (sz, av) => (sz ? ((sz - av) / sz) * 100 : null)),
            },
          ]
        : [];
    });
}
