import type { Point } from "../api";

export interface ChartSeries {
  label: string;
  points: Point[];
}

/** uPlot's columnar data: shared sorted x values, one y array per series (null = no sample). */
export function alignSeries(series: ChartSeries[]): (number | null)[][] {
  const xs = Array.from(new Set(series.flatMap((s) => s.points.map((p) => p[0])))).sort((a, b) => a - b);
  const index = new Map(xs.map((x, i) => [x, i]));
  const ys = series.map((s) => {
    const col: (number | null)[] = new Array<number | null>(xs.length).fill(null);
    for (const [t, v] of s.points) col[index.get(t)!] = v;
    return col;
  });
  return [xs, ...ys];
}

/** Combine two series point by point, using b's latest value at or before each of a's points. */
export function combine(a: Point[], b: Point[], op: (x: number, y: number) => number | null): Point[] {
  const out: Point[] = [];
  let j = 0;
  let last: number | null = null;
  for (const [t, x] of a) {
    while (j < b.length && b[j][0] <= t) last = b[j++][1];
    if (last === null) continue;
    const v = op(x, last);
    if (v !== null && Number.isFinite(v)) out.push([t, v]);
  }
  return out;
}

export const ratioPercent = (a: Point[], b: Point[]) => combine(a, b, (x, y) => (y ? (x / y) * 100 : null));
export const subtract = (a: Point[], b: Point[]) => combine(a, b, (x, y) => x - y);
