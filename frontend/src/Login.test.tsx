import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "./api";
import Login from "./Login";

afterEach(() => vi.restoreAllMocks());

function fill() {
  fireEvent.change(screen.getByLabelText("Username"), { target: { value: "alice" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "pw" } });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
}

describe("Login", () => {
  it("logs in and reports the user", async () => {
    const me = { user: { username: "alice", role: "admin" as const }, csrf: "c" };
    const spy = vi.spyOn(api, "login").mockResolvedValue(me);
    const onLogin = vi.fn();
    render(<Login onLogin={onLogin} />);
    fill();
    expect(screen.getByRole("button")).toHaveTextContent("Signing in…");
    await vi.waitFor(() => expect(onLogin).toHaveBeenCalledWith(me));
    expect(spy).toHaveBeenCalledWith("alice", "pw");
  });

  it("shows the error and re-enables the button", async () => {
    vi.spyOn(api, "login").mockRejectedValue(new api.ApiError(401, "invalid username or password"));
    render(<Login onLogin={vi.fn()} />);
    fill();
    expect(await screen.findByRole("alert")).toHaveTextContent("invalid username or password");
    expect(screen.getByRole("button")).toBeEnabled();
  });

  it("stringifies non-Error failures", async () => {
    vi.spyOn(api, "login").mockRejectedValue("boom");
    render(<Login onLogin={vi.fn()} />);
    fill();
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
  });
});
