import type { Labels, MetricQuery, QueryResult } from "../api";
import type { ChartSeries } from "./series";

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
