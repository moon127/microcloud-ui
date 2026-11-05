import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import * as api from "./api";

afterEach(() => vi.restoreAllMocks());

describe("App", () => {
  it("shows loading then backend version", async () => {
    vi.spyOn(api, "fetchHealth").mockResolvedValue({ status: "ok", version: "9.9" });
    render(<App />);
    expect(screen.getByRole("status")).toHaveTextContent("Checking backend");
    expect(await screen.findByText("Backend ok (v9.9)")).toBeInTheDocument();
  });

  it("shows error when backend unreachable", async () => {
    vi.spyOn(api, "fetchHealth").mockRejectedValue(new Error("boom"));
    render(<App />);
    expect(await screen.findByText("Backend unreachable: boom")).toBeInTheDocument();
  });

  it("stringifies non-Error rejections", async () => {
    vi.spyOn(api, "fetchHealth").mockRejectedValue("nope");
    render(<App />);
    expect(await screen.findByText("Backend unreachable: nope")).toBeInTheDocument();
  });

  it("ignores results after unmount", async () => {
    let resolve!: (h: api.Health) => void;
    vi.spyOn(api, "fetchHealth").mockReturnValue(new Promise((r) => (resolve = r)));
    const { unmount } = render(<App />);
    unmount();
    resolve({ status: "ok", version: "1" });
    await Promise.resolve();
  });
});
