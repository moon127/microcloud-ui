import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import * as api from "./api";

const me = { user: { username: "alice", role: "viewer" as const }, csrf: "c" };
const live: api.LiveMetrics = { scraped_at: null, interval_seconds: 15, members: [], instances: [] };

beforeEach(() => {
  vi.spyOn(api, "fetchServer").mockReturnValue(new Promise(() => {}));
  vi.spyOn(api, "fetchLiveMetrics").mockResolvedValue(live);
});
afterEach(() => vi.restoreAllMocks());

describe("App", () => {
  it("shows the dashboard for a logged-in user", async () => {
    vi.spyOn(api, "fetchMe").mockResolvedValue(me);
    render(<App />);
    expect(screen.getByRole("status")).toHaveTextContent("Checking session");
    expect(await screen.findByText("alice (viewer)")).toBeInTheDocument();
  });

  it("shows the login form when not logged in, then the dashboard", async () => {
    vi.spyOn(api, "fetchMe").mockRejectedValue(new api.ApiError(401, "not logged in"));
    vi.spyOn(api, "login").mockResolvedValue(me);
    render(<App />);
    fireEvent.change(await screen.findByLabelText("Username"), { target: { value: "alice" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "pw" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("alice (viewer)")).toBeInTheDocument();
  });

  it("signs out back to the login form even if logout fails", async () => {
    vi.spyOn(api, "fetchMe").mockResolvedValue(me);
    const out = vi.spyOn(api, "logout").mockRejectedValue(new Error("gone"));
    const csrf = vi.spyOn(api, "setCsrf");
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "Sign out" }));
    expect(await screen.findByLabelText("Username")).toBeInTheDocument();
    expect(out).toHaveBeenCalled();
    expect(csrf).toHaveBeenCalledWith(null);
  });

  it("shows an error when the backend is unreachable", async () => {
    vi.spyOn(api, "fetchMe").mockRejectedValue(new Error("boom"));
    render(<App />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Backend unreachable: boom");
  });

  it("stringifies non-Error rejections", async () => {
    vi.spyOn(api, "fetchMe").mockRejectedValue("nope");
    render(<App />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Backend unreachable: nope");
  });

  it("ignores results after unmount", async () => {
    let reject!: (e: unknown) => void;
    vi.spyOn(api, "fetchMe").mockReturnValue(new Promise((_, r) => (reject = r)));
    const { unmount } = render(<App />);
    unmount();
    reject(new Error("late"));
    await Promise.resolve();
  });
});
