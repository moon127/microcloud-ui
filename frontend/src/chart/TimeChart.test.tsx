import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { plots } from "../test/uplotMock";
import TimeChart from "./TimeChart";

afterEach(() => vi.unstubAllGlobals());

describe("TimeChart", () => {
  it("draws aligned series with formatted axes and legend values", () => {
    const fmt = (v: number) => `${v}!`;
    const { unmount } = render(
      <TimeChart title="CPU" format={fmt} series={[{ label: "a", points: [[1, 2]] }, { label: "b", points: [] }]} />,
    );
    expect(screen.getByRole("figure", { name: "CPU" })).toBeInTheDocument();
    expect(screen.getByTestId("uplot")).toBeInTheDocument();
    const p = plots[0];
    expect(p.data).toEqual([[1], [2], [null]]);
    expect(p.opts.series.map((s) => s.label)).toEqual([undefined, "a", "b"]);
    expect(p.opts.series[1].value!(null, 3)).toBe("3!");
    expect(p.opts.series[1].value!(null, null)).toBe("–");
    expect(p.opts.axes[1].values!(null, [1, 2])).toEqual(["1!", "2!"]);
    unmount();
    expect(p.destroyed).toBe(true);
  });

  it("follows container size when ResizeObserver exists", () => {
    let cb: () => void = () => {};
    const disconnect = vi.fn();
    vi.stubGlobal(
      "ResizeObserver",
      class {
        constructor(fn: () => void) {
          cb = fn;
        }
        observe() {}
        disconnect = disconnect;
      },
    );
    const { unmount } = render(<TimeChart title="x" series={[{ label: "a", points: [[1, 1]] }]} height={99} />);
    cb();
    expect(plots[0].sizes).toEqual([{ width: 0, height: 99 }]);
    unmount();
    expect(disconnect).toHaveBeenCalled();
  });

  it("shows a placeholder without data", () => {
    render(<TimeChart title="x" series={[{ label: "a", points: [] }]} />);
    expect(screen.getByText("No data yet for this range.")).toBeInTheDocument();
    expect(plots).toHaveLength(0);
  });
});
