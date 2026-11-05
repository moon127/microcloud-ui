// jsdom has no canvas: a stand-in that records what TimeChart asked uPlot to draw.
export interface FakePlot {
  opts: { series: { label?: string; value?: (u: unknown, v: number | null) => string }[]; axes: { values?: (u: unknown, v: number[]) => string[] }[] };
  data: (number | null)[][];
  destroyed: boolean;
  sizes: { width: number; height: number }[];
}

export const plots: FakePlot[] = [];

export default class FakeUPlot implements FakePlot {
  opts: FakePlot["opts"];
  data: FakePlot["data"];
  destroyed = false;
  sizes: FakePlot["sizes"] = [];
  constructor(opts: FakePlot["opts"], data: FakePlot["data"], el: HTMLElement) {
    this.opts = opts;
    this.data = data;
    const canvas = document.createElement("div");
    canvas.dataset.testid = "uplot";
    el.appendChild(canvas);
    plots.push(this);
  }
  setSize(size: { width: number; height: number }) {
    this.sizes.push(size);
  }
  destroy() {
    this.destroyed = true;
  }
}
