import { useEffect, useRef } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import { alignSeries, type ChartSeries } from "./series";

const COLORS = ["#e95420", "#0e8420", "#335280", "#c7162b", "#a06b00", "#7d4ea0", "#0f95a1", "#666666"];

interface Props {
  title: string;
  series: ChartSeries[];
  format?: (v: number) => string;
  height?: number;
}

function cssVar(name: string, fallback: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

export default function TimeChart({ title, series, format = String, height = 180 }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const empty = series.every((s) => s.points.length === 0);

  useEffect(() => {
    const el = ref.current;
    if (!el || empty) return;
    const muted = cssVar("--muted", "#888");
    const grid = { stroke: cssVar("--line", "#ddd"), width: 1 };
    const opts: uPlot.Options = {
      width: el.clientWidth || 600,
      height,
      series: [
        {},
        ...series.map((s, i) => ({
          label: s.label,
          stroke: COLORS[i % COLORS.length],
          width: 1.5,
          spanGaps: true,
          points: { show: false },
          value: (_u: uPlot, v: number | null) => (v == null ? "–" : format(v)),
        })),
      ],
      axes: [
        { stroke: muted, grid },
        { stroke: muted, grid, size: 72, values: (_u: uPlot, vals: number[]) => vals.map(format) },
      ],
      legend: { live: true },
    };
    const plot = new uPlot(opts, alignSeries(series) as uPlot.AlignedData, el);
    const ro =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(() => plot.setSize({ width: el.clientWidth, height }));
    ro?.observe(el);
    return () => {
      ro?.disconnect();
      plot.destroy();
    };
  }, [series, format, height, empty]);

  return (
    <figure className="chart" aria-label={title}>
      <figcaption>{title}</figcaption>
      {empty ? <p className="muted">No data yet for this range.</p> : <div ref={ref} />}
    </figure>
  );
}
