import { act, fireEvent, screen } from "@testing-library/react";
import { Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import TimeChart from "./chart/TimeChart";
import Layout from "./Layout";
import { renderAt } from "./test/render";
import { plots } from "./test/uplotMock";
import { applyTheme, resolvedTheme, storedTheme } from "./theme";

/** A matchMedia stand-in whose dark preference can be flipped. */
function fakeMedia(dark: boolean) {
  const listeners = new Set<() => void>();
  const mq = {
    get matches() {
      return dark;
    },
    addEventListener: (_: string, f: () => void) => listeners.add(f),
    removeEventListener: (_: string, f: () => void) => listeners.delete(f),
  };
  vi.stubGlobal("matchMedia", () => mq);
  return {
    set(d: boolean) {
      dark = d;
      listeners.forEach((f) => f());
    },
    listeners,
  };
}

afterEach(() => {
  applyTheme("system");
  localStorage.clear();
  document.documentElement.style.cssText = "";
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("theme", () => {
  it("remembers the choice and marks the page", () => {
    expect(storedTheme()).toBe("system");
    applyTheme("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem("mcui.theme")).toBe("dark");
    expect(storedTheme()).toBe("dark");
    expect(resolvedTheme()).toBe("dark");
    applyTheme("light", false);
    expect(localStorage.getItem("mcui.theme")).toBe("dark"); // not remembered
    applyTheme("system");
    expect(document.documentElement.dataset.theme).toBeUndefined();
    expect(localStorage.getItem("mcui.theme")).toBeNull();
    localStorage.setItem("mcui.theme", "purple");
    expect(storedTheme()).toBe("system");
  });

  it("follows the OS on system, and copes without storage or matchMedia", () => {
    vi.stubGlobal("matchMedia", undefined);
    expect(resolvedTheme()).toBe("light");
    const media = fakeMedia(true);
    expect(resolvedTheme()).toBe("dark");
    media.set(false);
    expect(resolvedTheme()).toBe("light");
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(storedTheme()).toBe("system");
    applyTheme("dark"); // still applied for this page
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("switches from the top bar", () => {
    renderAt(
      <Routes>
        <Route element={<Layout />}>
          <Route path="*" element={<p>child</p>} />
        </Route>
      </Routes>,
    );
    const select = screen.getByLabelText("Theme");
    expect(select).toHaveValue("system");
    fireEvent.change(select, { target: { value: "dark" } });
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(select).toHaveValue("dark");
    fireEvent.change(select, { target: { value: "system" } });
    expect(document.documentElement.dataset.theme).toBeUndefined();
  });

  it("charts take their colours from the theme and redraw when it changes", () => {
    const media = fakeMedia(false);
    document.documentElement.style.setProperty("--series-0", "#111111");
    renderAt(<TimeChart title="t" series={[{ label: "a", points: [[1, 1]] }]} />);
    expect(plots.at(-1)!.opts.series[1]).toMatchObject({ stroke: "#111111" });
    document.documentElement.style.setProperty("--series-0", "#eeeeee");
    act(() => media.set(true));
    expect(plots.at(-1)!.opts.series[1]).toMatchObject({ stroke: "#eeeeee" });
    expect(plots.filter((p) => p.destroyed)).toHaveLength(1);
    act(() => applyTheme("light"));
    expect(plots).toHaveLength(3);
  });
});
