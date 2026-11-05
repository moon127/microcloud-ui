import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "./api";
import { ApiError, fetchHealth, fetchLiveMetrics, fetchMe, fetchServer, login, logout, setCsrf } from "./api";

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
    await api.api("/a", {}, f);
    await api.api("/b", { method: "post", body: "{}" }, f);
    expect((f.mock.calls[0][1] as RequestInit).credentials).toBe("same-origin");
    expect(headersOf(f, 0).get("X-CSRF-Token")).toBeNull();
    expect(headersOf(f, 0).get("Content-Type")).toBeNull();
    expect((f.mock.calls[1][1] as RequestInit).method).toBe("POST");
    expect(headersOf(f, 1).get("X-CSRF-Token")).toBe("tok");
    expect(headersOf(f, 1).get("Content-Type")).toBe("application/json");
  });

  it("omits CSRF header when no token is known", async () => {
    const f = vi.fn().mockResolvedValue(json({}));
    await api.api("/b", { method: "DELETE" }, f);
    expect(headersOf(f).get("X-CSRF-Token")).toBeNull();
  });

  it("raises ApiError with the backend detail", async () => {
    const f = vi.fn().mockResolvedValue(json({ detail: "LXD: Forbidden" }, 502));
    const err = await api.api("/x", {}, f).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(502);
    expect((err as ApiError).message).toBe("LXD: Forbidden");
  });

  it("falls back to the status when detail is missing or not a string", async () => {
    const f = vi.fn().mockResolvedValue(json({ detail: [{ msg: "x" }] }, 422));
    await expect(api.api("/x", {}, f)).rejects.toThrow("HTTP 422");
  });

  it("returns undefined for 204", async () => {
    const f = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    await expect(api.api("/x", { method: "POST" }, f)).resolves.toBeUndefined();
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
    await api.api("/api/x", { method: "POST" });
    expect(((f.mock.calls[3][1] as RequestInit).headers as Headers).get("X-CSRF-Token")).toBe("c2");
  });

  it("fetchServer and fetchLiveMetrics hit their endpoints", async () => {
    const f = vi.spyOn(globalThis, "fetch").mockImplementation(() => Promise.resolve(json({})));
    await fetchServer();
    await fetchLiveMetrics();
    expect(f.mock.calls.map((c) => c[0])).toEqual(["/api/server", "/api/metrics/live"]);
  });
});

describe("history and inventory helpers", () => {
  it("build the right URLs", async () => {
    const f = vi.spyOn(globalThis, "fetch").mockImplementation(() => Promise.resolve(json({})));
    await api.queryMetric({ name: "m", range: 900, fn: "rate", match: { project: "p", name: "a b" }, by: ["device", "name"] });
    await api.queryMetric({ name: "m", range: 60 });
    await api.fetchTop("disk", 3600, 5);
    await api.fetchTop("cpu", 60);
    await api.fetchInstances();
    await api.fetchInstance("my proj", "a/b");
    await api.fetchMembers();
    await api.fetchMemberResources("m 1");
    await api.fetchNetworkTraffic(86400);
    expect(f.mock.calls.map((c) => c[0])).toEqual([
      "/api/metrics/query?name=m&range=900&fn=rate&match=project%3Dp&match=name%3Da+b&by=device&by=name",
      "/api/metrics/query?name=m&range=60",
      "/api/metrics/top?metric=disk&range=3600&limit=5",
      "/api/metrics/top?metric=cpu&range=60&limit=10",
      "/api/instances",
      "/api/instances/my%20proj/a%2Fb",
      "/api/cluster/members",
      "/api/cluster/members/m%201/resources",
      "/api/metrics/network-traffic?range=86400",
    ]);
  });
});

describe("phase 3 helpers", () => {
  it("send the right method, URL and body", async () => {
    setCsrf("t");
    const f = vi.spyOn(globalThis, "fetch").mockImplementation(() => Promise.resolve(json({})));
    await api.fetchOperations();
    await api.waitOperation("a/b");
    await api.waitOperation("x", 5);
    await api.cancelOperation("op 1");
    await api.fetchWarnings();
    await api.setWarningStatus("w1", "acknowledged");
    await api.deleteWarning("w1");
    await api.evacuateMember("m 1", "stop");
    await api.evacuateMember("m1");
    await api.restoreMember("m1");
    await api.fetchProjects();
    const calls = f.mock.calls.map(([url, init]) => [url, (init as RequestInit).method, (init as RequestInit).body ?? null]);
    expect(calls).toEqual([
      ["/api/operations", "GET", null],
      ["/api/operations/a%2Fb?wait=30", "GET", null],
      ["/api/operations/x?wait=5", "GET", null],
      ["/api/operations/op%201", "DELETE", null],
      ["/api/warnings", "GET", null],
      ["/api/warnings/w1", "PUT", '{"status":"acknowledged"}'],
      ["/api/warnings/w1", "DELETE", null],
      ["/api/cluster/members/m%201/evacuate", "POST", '{"mode":"stop"}'],
      ["/api/cluster/members/m1/evacuate", "POST", "{}"],
      ["/api/cluster/members/m1/restore", "POST", "{}"],
      ["/api/projects", "GET", null],
    ]);
  });
});

describe("instance helpers", () => {
  it("send the right method, URL and body", async () => {
    setCsrf("t");
    const f = vi.spyOn(globalThis, "fetch").mockImplementation(() => Promise.resolve(json({})));
    const upd = { description: "", config: {}, devices: {}, profiles: [], ephemeral: false, stateful: false };
    await api.fetchInstance("p", "c 1");
    await api.changeState("p", "c1", "stop", true);
    await api.changeState("p", "c1", "start");
    await api.deleteInstance("p", "c1");
    await api.deleteInstance("p", "c1", true);
    await api.updateInstance("p", "c1", upd);
    await api.patchInstanceConfig("p", "c1", { a: "1" });
    await api.renameInstance("p", "c1", "c2");
    await api.moveInstance("p", "c1", { target: "m2" });
    await api.copyInstance("p", "c1", { name: "c3", snapshot: "s1" });
    await api.createSnapshot("p", "c1", { name: "s1" });
    await api.renameSnapshot("p", "c1", "s 1", "s2");
    await api.deleteSnapshot("p", "c1", "s1");
    await api.restoreSnapshot("p", "c1", "s1");
    await api.publishInstance("p", "c1", { alias: "g" });
    await api.fetchLogs("p", "c1");
    await api.fetchStoragePools();
    const calls = f.mock.calls.map(([url, init]) => [url, (init as RequestInit).method, (init as RequestInit).body ?? null]);
    expect(calls).toEqual([
      ["/api/instances/p/c%201", "GET", null],
      ["/api/instances/p/c1/state", "PUT", '{"action":"stop","force":true}'],
      ["/api/instances/p/c1/state", "PUT", '{"action":"start","force":false}'],
      ["/api/instances/p/c1", "DELETE", null],
      ["/api/instances/p/c1?force=true", "DELETE", null],
      ["/api/instances/p/c1", "PUT", JSON.stringify(upd)],
      ["/api/instances/p/c1/config", "PATCH", '{"config":{"a":"1"}}'],
      ["/api/instances/p/c1/rename", "POST", '{"name":"c2"}'],
      ["/api/instances/p/c1/move", "POST", '{"target":"m2"}'],
      ["/api/instances/p/c1/copy", "POST", '{"name":"c3","snapshot":"s1"}'],
      ["/api/instances/p/c1/snapshots", "POST", '{"name":"s1"}'],
      ["/api/instances/p/c1/snapshots/s%201/rename", "POST", '{"name":"s2"}'],
      ["/api/instances/p/c1/snapshots/s1", "DELETE", null],
      ["/api/instances/p/c1/snapshots/s1/restore", "POST", "{}"],
      ["/api/instances/p/c1/publish", "POST", '{"alias":"g"}'],
      ["/api/instances/p/c1/logs", "GET", null],
      ["/api/storage-pools", "GET", null],
    ]);
  });

  it("fetches logs as text", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response("line\n"))
      .mockResolvedValueOnce(new Response("", { status: 404 }));
    await expect(api.fetchLog("p", "c1", "lxc.log")).resolves.toBe("line\n");
    await expect(api.fetchLog("p", "c1", "x")).rejects.toThrow("HTTP 404");
  });
});

describe("create-instance helpers", () => {
  it("send the right method, URL and body", async () => {
    setCsrf("t");
    const f = vi.spyOn(globalThis, "fetch").mockImplementation(() => Promise.resolve(json({})));
    const body: api.NewInstance = {
      name: "a",
      type: "container",
      project: "p",
      image: { remote: "images", alias: "alpine/edge" },
      description: "",
      profiles: ["default"],
      config: {},
      devices: {},
      start: true,
    };
    await api.fetchLocalImages("my p");
    await api.fetchRemoteImages("ubuntu");
    await api.fetchProfiles("p");
    await api.fetchNetworks("p");
    await api.createInstance(body);
    const calls = f.mock.calls.map(([url, init]) => [url, (init as RequestInit).method, (init as RequestInit).body ?? null]);
    expect(calls).toEqual([
      ["/api/images?project=my%20p", "GET", null],
      ["/api/images/remote/ubuntu", "GET", null],
      ["/api/profiles?project=p", "GET", null],
      ["/api/networks?project=p", "GET", null],
      ["/api/instances", "POST", JSON.stringify(body)],
    ]);
  });
});
