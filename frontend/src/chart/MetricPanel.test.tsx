import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { renderAt } from "../test/render";
import { plots } from "../test/uplotMock";
import MetricPanel from "./MetricPanel";

afterEach(() => vi.restoreAllMocks());

const result = (series: api.Series[]): api.QueryResult => ({
  name: "m",
  kind: "gauge",
  resolution: 0,
  fn: "value",
  start: 0,
  end: 1,
  series,
});

describe("MetricPanel", () => {
  it("queries each metric with the range and labels series", async () => {
    const q = vi.spyOn(api, "queryMetric").mockResolvedValue(result([{ labels: { dev: "sda" }, points: [[1, 5]] }]));
    renderAt(
      <MetricPanel
        title="Disk"
        range={900}
        queries={[{ name: "a", fn: "rate", match: { name: "x" }, label: (l) => l.dev }, { name: "b" }]}
      />,
    );
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    expect(await screen.findByTestId("uplot")).toBeInTheDocument();
    expect(q).toHaveBeenCalledWith({ name: "a", fn: "rate", match: { name: "x" }, label: expect.any(Function), range: 900 });
    expect(plots[0].opts.series.map((s) => s.label)).toEqual([undefined, "sda", "b"]);
  });

  it("uses compute when given", async () => {
    vi.spyOn(api, "queryMetric").mockResolvedValue(result([]));
    renderAt(<MetricPanel title="X" range={60} queries={[{ name: "a" }]} compute={() => [{ label: "c", points: [[1, 1]] }]} />);
    await screen.findByTestId("uplot");
    expect(plots[0].opts.series[1].label).toBe("c");
  });

  it("shows errors", async () => {
    vi.spyOn(api, "queryMetric").mockRejectedValue(new Error("nope"));
    renderAt(<MetricPanel title="X" range={60} queries={[{ name: "a" }]} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("nope");
  });
});
