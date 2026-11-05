import { describe, expect, it } from "vitest";
import { alignSeries, combine, ratioPercent, subtract } from "./series";
import { defaultSeries, nonZero, poolSeries, refreshFor } from "./panel";
import type { QueryResult } from "../api";

const qr = (series: QueryResult["series"]): QueryResult => ({
  name: "x",
  kind: "gauge",
  resolution: 0,
  fn: "value",
  start: 0,
  end: 1,
  series,
});

describe("series helpers", () => {
  it("aligns series on shared timestamps", () => {
    expect(
      alignSeries([
        { label: "a", points: [[10, 1], [20, 2]] },
        { label: "b", points: [[15, 5], [20, 6]] },
      ]),
    ).toEqual([[10, 15, 20], [1, null, 2], [null, 5, 6]]);
    expect(alignSeries([])).toEqual([[]]);
  });

  it("combines using b's latest value at or before each point", () => {
    const a: [number, number][] = [[5, 9], [10, 1], [20, 2], [30, 3]];
    const b: [number, number][] = [[10, 4], [25, 0]];
    expect(ratioPercent(a, b)).toEqual([[10, 25], [20, 50]]); // 30: divisor 0 dropped; 5: no b yet
    expect(subtract(a, b)).toEqual([[10, -3], [20, -2], [30, 3]]);
    expect(combine(a, b, () => Infinity)).toEqual([]);
  });

  it("builds default series and drops all-zero ones", () => {
    const queries = [{ name: "m", label: (l: Record<string, string>) => l.dev }, { name: "n" }];
    const results = [qr([{ labels: { dev: "x" }, points: [[1, 0]] }, { labels: { dev: "y" }, points: [[1, 2]] }]), qr([{ labels: {}, points: [[1, 3]] }])];
    expect(defaultSeries(queries, results).map((s) => s.label)).toEqual(["x", "y", "n"]);
    expect(nonZero(queries)(results).map((s) => s.label)).toEqual(["y", "n"]);
  });

  it("refreshes only short ranges", () => {
    expect(refreshFor(3600)).toBe(60_000);
    expect(refreshFor(86400)).toBeUndefined();
  });

  it("shows a shared pool size once, differing sizes per member", () => {
    const used = qr([{ labels: { member: "m1" }, points: [[1, 5]] }]);
    const same = qr([
      { labels: { member: "m1" }, points: [[1, 10]] },
      { labels: { member: "m2" }, points: [[1, 10]] },
    ]);
    expect(poolSeries([used, same]).map((s) => s.label)).toEqual(["m1 used", "size"]);
    const differ = qr([
      { labels: { member: "m1" }, points: [[1, 10]] },
      { labels: { member: "m2" }, points: [[1, 20]] },
    ]);
    expect(poolSeries([used, differ]).map((s) => s.label)).toEqual(["m1 used", "m1 size", "m2 size"]);
    expect(poolSeries([used, qr([])]).map((s) => s.label)).toEqual(["m1 used"]);
  });
});
