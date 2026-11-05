import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, fetchHealth, fetchLiveMetrics, fetchMe, fetchServer, login, logout, setCsrf } from "./api";

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

afterEach(() => {
  vi.restoreAllMocks();
  setCsrf(null);
});

function headersOf(f: ReturnType<typeof vi.fn>, call = 0): Headers {
  return (f.mock.calls[call][1] as RequestInit).headers as Headers;
}

describe("fetchHealth", () => {
  it("returns parsed health on 200", async () => {
    const f = vi.fn().mockResolvedValue(json({ status: "ok", version: "1" }));
    await expect(fetchHealth(f)).resolves.toEqual({ status: "ok", version: "1" });
    expect(f.mock.calls[0][0]).toBe("/api/health");
  });

  it("throws on non-2xx", async () => {
    const f = vi.fn().mockResolvedValue(new Response("", { status: 503 }));
    await expect(fetchHealth(f)).rejects.toThrow("HTTP 503");
  });

  it("uses global fetch by default", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(json({ status: "ok", version: "2" }));
    await expect(fetchHealth()).resolves.toMatchObject({ version: "2" });
  });
});

describe("api", () => {
  it("sends credentials, JSON content type and CSRF only on unsafe methods", async () => {
    const f = vi.fn().mockImplementation(() => Promise.resolve(json({})));
    setCsrf("tok");
    await api("/a", {}, f);
    await api("/b", { method: "post", body: "{}" }, f);
    expect((f.mock.calls[0][1] as RequestInit).credentials).toBe("same-origin");
    expect(headersOf(f, 0).get("X-CSRF-Token")).toBeNull();
    expect(headersOf(f, 0).get("Content-Type")).toBeNull();
    expect((f.mock.calls[1][1] as RequestInit).method).toBe("POST");
    expect(headersOf(f, 1).get("X-CSRF-Token")).toBe("tok");
    expect(headersOf(f, 1).get("Content-Type")).toBe("application/json");
  });

  it("omits CSRF header when no token is known", async () => {
    const f = vi.fn().mockResolvedValue(json({}));
    await api("/b", { method: "DELETE" }, f);
    expect(headersOf(f).get("X-CSRF-Token")).toBeNull();
  });

  it("raises ApiError with the backend detail", async () => {
    const f = vi.fn().mockResolvedValue(json({ detail: "LXD: Forbidden" }, 502));
    const err = await api("/x", {}, f).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(502);
    expect((err as ApiError).message).toBe("LXD: Forbidden");
  });

  it("falls back to the status when detail is missing or not a string", async () => {
    const f = vi.fn().mockResolvedValue(json({ detail: [{ msg: "x" }] }, 422));
    await expect(api("/x", {}, f)).rejects.toThrow("HTTP 422");
  });

  it("returns undefined for 204", async () => {
    const f = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    await expect(api("/x", { method: "POST" }, f)).resolves.toBeUndefined();
  });
});

describe("session helpers", () => {
  it("login, me and logout manage the CSRF token", async () => {
    const f = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(json({ user: { username: "a", role: "admin" }, csrf: "c1" }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(json({ user: { username: "a", role: "admin" }, csrf: "c2" }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    const me = await login("a", "pw");
    expect(me.user.username).toBe("a");
    expect(JSON.parse((f.mock.calls[0][1] as RequestInit).body as string)).toEqual({ username: "a", password: "pw" });
    await logout();
    expect(((f.mock.calls[1][1] as RequestInit).headers as Headers).get("X-CSRF-Token")).toBe("c1");
    await fetchMe();
    await api("/api/x", { method: "POST" });
    expect(((f.mock.calls[3][1] as RequestInit).headers as Headers).get("X-CSRF-Token")).toBe("c2");
  });

  it("fetchServer and fetchLiveMetrics hit their endpoints", async () => {
    const f = vi.spyOn(globalThis, "fetch").mockImplementation(() => Promise.resolve(json({})));
    await fetchServer();
    await fetchLiveMetrics();
    expect(f.mock.calls.map((c) => c[0])).toEqual(["/api/server", "/api/metrics/live"]);
  });
});
