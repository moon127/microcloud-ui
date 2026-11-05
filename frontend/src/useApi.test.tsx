import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import { SessionContext } from "./session";
import { session } from "./test/render";
import { useApi } from "./useApi";

afterEach(() => vi.useRealTimers());

function Probe({ k, load, refresh }: { k: string; load: () => Promise<string>; refresh?: number }) {
  const s = useApi(k, load, refresh);
  return <p>{s.loading ? "loading" : s.error ? `error:${s.error}` : `data:${s.data}`}</p>;
}

describe("useApi", () => {
  it("loads, refreshes and reloads on key change", async () => {
    vi.useFakeTimers();
    const load = vi.fn().mockResolvedValueOnce("a").mockResolvedValueOnce("b").mockResolvedValue("c");
    const { rerender, unmount } = render(<Probe k="1" load={load} refresh={1000} />);
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(screen.getByText("data:a")).toBeInTheDocument();
    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(screen.getByText("data:b")).toBeInTheDocument();
    rerender(<Probe k="2" load={load} refresh={1000} />);
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(screen.getByText("data:c")).toBeInTheDocument();
    unmount();
    await act(() => vi.advanceTimersByTimeAsync(5000));
    expect(load).toHaveBeenCalledTimes(3);
  });

  it("reports errors and stringifies non-Errors", async () => {
    render(<Probe k="1" load={() => Promise.reject(new Error("x"))} />);
    expect(await screen.findByText("error:x")).toBeInTheDocument();
    render(<Probe k="2" load={() => Promise.reject("y")} />);
    expect(await screen.findByText("error:y")).toBeInTheDocument();
  });

  it("ends the session on 401", async () => {
    const s = session();
    render(
      <SessionContext.Provider value={s}>
        <Probe k="1" load={() => Promise.reject(new ApiError(401, "no"))} />
      </SessionContext.Provider>,
    );
    await vi.waitFor(() => expect(s.onExpired).toHaveBeenCalled());
    expect(screen.getByText("loading")).toBeInTheDocument();
    // without a session a 401 is simply ignored
    render(<Probe k="2" load={() => Promise.reject(new ApiError(401, "no"))} />);
  });

  it("ignores results after unmount", async () => {
    let resolve!: (v: string) => void;
    let reject!: (e: unknown) => void;
    const a = render(<Probe k="1" load={() => new Promise((r) => (resolve = r))} />);
    const b = render(<Probe k="2" load={() => new Promise((_, r) => (reject = r))} />);
    a.unmount();
    b.unmount();
    resolve("late");
    reject(new Error("late"));
    await Promise.resolve();
  });
});
