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
    await api.evacuateMember("m1", undefined, true);
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
      ["/api/cluster/members/m1/evacuate", "POST", '{"force":true}'],
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
    await api.restoreSnapshot("p", "c1", "s1", true);
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
      ["/api/instances/p/c1/snapshots/s1/restore", "POST", '{"disk_volumes_mode":"all-exclusive"}'],
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
    await api.fetchPlacementGroups("p");
    const pg = { description: "", policy: "spread" as const, rigor: "strict" as const };
    await api.createPlacementGroup("p", "web", pg);
    await api.updatePlacementGroup("p", "w b", pg);
    await api.deletePlacementGroup("p", "web");
    const calls = f.mock.calls.map(([url, init]) => [url, (init as RequestInit).method, (init as RequestInit).body ?? null]);
    expect(calls).toEqual([
      ["/api/images?project=my%20p", "GET", null],
      ["/api/images/remote/ubuntu", "GET", null],
      ["/api/profiles?project=p", "GET", null],
      ["/api/networks?project=p", "GET", null],
      ["/api/instances", "POST", JSON.stringify(body)],
      ["/api/placement-groups?project=p", "GET", null],
      ["/api/placement-groups", "POST", JSON.stringify({ name: "web", project: "p", ...{ description: "", policy: "spread", rigor: "strict" } })],
      ["/api/placement-groups/p/w%20b", "PUT", JSON.stringify({ description: "", policy: "spread", rigor: "strict" })],
      ["/api/placement-groups/p/web", "DELETE", null],
    ]);
  });
});

describe("console helpers", () => {
  it("send the right method, URL and body; fetch logs as text", async () => {
    setCsrf("t");
    const f = vi.spyOn(globalThis, "fetch").mockImplementation(() => Promise.resolve(json({})));
    await api.startExec("p", "c1", { cols: 80, rows: 24 });
    await api.startConsole("p", "c1", { type: "vga" });
    await api.clearConsoleLog("p", "c1");
    const calls = f.mock.calls.map(([url, init]) => [url, (init as RequestInit).method, (init as RequestInit).body ?? null]);
    expect(calls).toEqual([
      ["/api/instances/p/c1/exec", "POST", '{"cols":80,"rows":24}'],
      ["/api/instances/p/c1/console", "POST", '{"type":"vga"}'],
      ["/api/instances/p/c1/console-log", "DELETE", null],
    ]);
    f.mockResolvedValueOnce(new Response("boot\n")).mockResolvedValueOnce(new Response("LXD: not running", { status: 502 }));
    f.mockResolvedValueOnce(new Response("", { status: 500 }));
    await expect(api.fetchConsoleLog("p", "c1")).resolves.toBe("boot\n");
    await expect(api.fetchConsoleLog("p", "c1")).rejects.toThrow("LXD: not running");
    await expect(api.fetchConsoleLog("p", "c1")).rejects.toThrow("HTTP 500");
    expect(api.streamUrl("a b")).toBe(`ws://${window.location.host}/api/streams/a%20b`);
    vi.stubGlobal("location", { ...window.location, protocol: "https:", host: "ui.example" });
    expect(api.streamUrl("x")).toBe("wss://ui.example/api/streams/x");
    vi.unstubAllGlobals();
  });
});

describe("operationProgress", () => {
  const op = (metadata: api.Operation["metadata"]): api.Operation => ({ id: "o", description: "d", created_at: "", status: "Running", metadata });
  it("reads LXD's progress notes", () => {
    expect(api.operationProgress(op(null))).toBeNull();
    expect(api.operationProgress(op({ fds: {}, download_progress: 7 }))).toBeNull();
    expect(api.operationProgress(op({ download_progress: "rootfs: 45% (12.3MB/s)" }))).toBe("rootfs: 45% (12.3MB/s)");
    expect(api.operationProgress(op({ progress: { stage: "unpack", percent: "80", speed: "5MB/s" }, create_instance_from_image_unpack_progress: "Unpack: 80%" }))).toBe(
      "unpack 80% (5MB/s) · Unpack: 80%",
    );
    expect(api.operationProgress(op({ progress: { stage: "copy" } }))).toBe("copy");
  });
});

describe("OVN load balancers (Phase 5b)", () => {
  it("build the right URLs", async () => {
    const f = vi.spyOn(globalThis, "fetch").mockImplementation(() => Promise.resolve(json({})));
    const lb: api.LbBody = { description: "", config: {}, backends: [], ports: [] };
    const pool: api.PoolBody = { description: "", protocol: "tcp", target_port: "80", instances: ["c1"], healthcheck: false };
    await api.fetchLoadBalancers("p", "o n");
    await api.createLoadBalancer("p", "ovn", { listen_address: "0.0.0.0", ...lb });
    await api.updateLoadBalancer("p", "ovn", "198.51.100.1", lb);
    await api.deleteLoadBalancer("p", "ovn", "198.51.100.1");
    await api.fetchLbPools("p", "ovn");
    await api.fetchLbPoolState("p", "ovn", "web");
    await api.createLbPool("p", "ovn", "web", pool);
    await api.updateLbPool("p", "ovn", "web", pool);
    await api.deleteLbPool("p", "ovn", "web");
    const calls = f.mock.calls.map(([url, init]) => [url, (init as RequestInit).method, (init as RequestInit).body ?? null]);
    const B = "/api/networks/p/ovn";
    expect(calls).toEqual([
      ["/api/networks/p/o%20n/load-balancers", "GET", null],
      [`${B}/load-balancers`, "POST", JSON.stringify({ listen_address: "0.0.0.0", ...lb })],
      [`${B}/load-balancers/198.51.100.1`, "PUT", JSON.stringify(lb)],
      [`${B}/load-balancers/198.51.100.1`, "DELETE", null],
      [`${B}/load-balancer-pools`, "GET", null],
      [`${B}/load-balancer-pools/web/state`, "GET", null],
      [`${B}/load-balancer-pools`, "POST", JSON.stringify({ name: "web", ...pool })],
      [`${B}/load-balancer-pools/web`, "PUT", JSON.stringify(pool)],
      [`${B}/load-balancer-pools/web`, "DELETE", null],
    ]);
  });
});

describe("image registries (Phase 5b)", () => {
  it("build the right URLs", async () => {
    const f = vi.spyOn(globalThis, "fetch").mockImplementation(() => Promise.resolve(json({})));
    await api.fetchImageRegistries();
    await api.createImageRegistry({ name: "m", url: "https://m.example.org", description: "" });
    await api.deleteImageRegistry("m 2");
    await api.fetchRemoteImages("lab-mirror");
    const calls = f.mock.calls.map(([url, init]) => [url, (init as RequestInit).method, (init as RequestInit).body ?? null]);
    expect(calls).toEqual([
      ["/api/image-registries", "GET", null],
      ["/api/image-registries", "POST", JSON.stringify({ name: "m", url: "https://m.example.org", description: "" })],
      ["/api/image-registries/m%202", "DELETE", null],
      ["/api/images/remote/lab-mirror", "GET", null],
    ]);
  });
});

describe("backups (Phase 7a)", () => {
  it("send the right method, URL and body", async () => {
    setCsrf("t");
    const f = vi.spyOn(globalThis, "fetch").mockImplementation(() => Promise.resolve(json({})));
    await api.fetchBackups("p", "c1");
    await api.createBackup("p", "c1", { name: "b1", instance_only: true, optimized_storage: false });
    await api.renameBackup("p", "c1", "b 1", "b2");
    await api.deleteBackup("p", "c1", "b1");
    const calls = f.mock.calls.map(([url, init]) => [url, (init as RequestInit).method, (init as RequestInit).body ?? null]);
    expect(calls).toEqual([
      ["/api/instances/p/c1/backups", "GET", null],
      ["/api/instances/p/c1/backups", "POST", '{"name":"b1","instance_only":true,"optimized_storage":false}'],
      ["/api/instances/p/c1/backups/b%201/rename", "POST", '{"name":"b2"}'],
      ["/api/instances/p/c1/backups/b1", "DELETE", null],
    ]);
    expect(api.backupExportUrl("p", "c1", "b1")).toBe("/api/instances/p/c1/backups/b1/export");
  });
});

/** Just enough of XMLHttpRequest: records the request, answers when told. */
class FakeXhr {
  static last: FakeXhr;
  method = "";
  url = "";
  headers: Record<string, string> = {};
  body: unknown;
  status = 0;
  responseText = "";
  upload: { onprogress?: (e: { lengthComputable: boolean; loaded: number; total: number }) => void } = {};
  onload?: () => void;
  onerror?: () => void;
  constructor() {
    FakeXhr.last = this;
  }
  open(method: string, url: string) {
    this.method = method;
    this.url = url;
  }
  setRequestHeader(k: string, v: string) {
    this.headers[k] = v;
  }
  send(body: unknown) {
    this.body = body;
  }
  answer(status: number, text: string) {
    this.status = status;
    this.responseText = text;
    this.onload?.();
  }
}

describe("uploadWithProgress", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("posts the file with the CSRF token and reports progress", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    setCsrf("t");
    const seen: number[] = [];
    const file = new Blob(["x"]);
    const p = api.importBackup(file, { project: "p", name: "c2", pool: "" }, (f) => seen.push(f));
    const xhr = FakeXhr.last;
    expect([xhr.method, xhr.url, xhr.body]).toEqual(["POST", "/api/instances/import?project=p&name=c2", file]);
    expect(xhr.headers).toEqual({ "Content-Type": "application/octet-stream", "X-CSRF-Token": "t" });
    xhr.upload.onprogress?.({ lengthComputable: true, loaded: 5, total: 10 });
    xhr.upload.onprogress?.({ lengthComputable: false, loaded: 5, total: 0 });
    xhr.answer(200, JSON.stringify({ operation: null, metadata: null }));
    await expect(p).resolves.toEqual({ operation: null, metadata: null });
    expect(seen).toEqual([0.5]);
  });

  it("reports the server's error, a bare status, or a lost connection", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    let p = api.uploadWithProgress("/u", new Blob([]));
    expect(FakeXhr.last.headers["X-CSRF-Token"]).toBeUndefined();
    FakeXhr.last.answer(403, JSON.stringify({ detail: "needs the manage right in p" }));
    await expect(p).rejects.toThrow("needs the manage right in p");
    p = api.uploadWithProgress("/u", new Blob([]));
    FakeXhr.last.answer(502, "<html>bad gateway</html>");
    await expect(p).rejects.toThrow("HTTP 502");
    p = api.uploadWithProgress("/u", new Blob([]));
    FakeXhr.last.onerror?.();
    await expect(p).rejects.toMatchObject({ status: 0, message: "the upload was interrupted" });
  });
});

describe("volume backups (Phase 7b)", () => {
  it("send the right method, URL and body", async () => {
    const f = vi.spyOn(globalThis, "fetch").mockImplementation(() => Promise.resolve(json({})));
    await api.fetchVolumeBackups("p", "local", "v1", "m1");
    await api.createVolumeBackup("p", "remote", "v1", { name: "b1", volume_only: true, optimized_storage: false });
    await api.renameVolumeBackup("p", "local", "v1", "b 1", "b2", "m1");
    await api.deleteVolumeBackup("p", "local", "v1", "b1", "m1");
    const calls = f.mock.calls.map(([url, init]) => [url, (init as RequestInit).method, (init as RequestInit).body ?? null]);
    expect(calls).toEqual([
      ["/api/volumes/p/local/v1/backups?target=m1", "GET", null],
      ["/api/volumes/p/remote/v1/backups", "POST", '{"name":"b1","volume_only":true,"optimized_storage":false}'],
      ["/api/volumes/p/local/v1/backups/b%201/rename?target=m1", "POST", '{"name":"b2"}'],
      ["/api/volumes/p/local/v1/backups/b1?target=m1", "DELETE", null],
    ]);
    expect(api.volumeBackupExportUrl("p", "remote", "v1", "b1")).toBe("/api/volumes/p/remote/v1/backups/b1/export");
  });

  it("imports through the progress upload", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    const p = api.importVolumeBackup(new Blob(["x"]), { project: "p", pool: "local", name: "", target: "m2" });
    expect(FakeXhr.last.url).toBe("/api/volumes/import?project=p&pool=local&target=m2");
    FakeXhr.last.answer(200, "{}");
    await expect(p).resolves.toEqual({});
    vi.unstubAllGlobals();
  });
});

describe("ISO upload (Phase 7d)", () => {
  it("posts to /api/volumes/iso", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    const p = api.uploadIso(new Blob(["x"]), { project: "p", pool: "remote", name: "u" });
    expect(FakeXhr.last.url).toBe("/api/volumes/iso?project=p&pool=remote&name=u");
    FakeXhr.last.answer(200, "{}");
    await expect(p).resolves.toEqual({});
    vi.unstubAllGlobals();
  });
});

describe("network peers (Phase 10a)", () => {
  it("send the right method, URL and body", async () => {
    const f = vi.spyOn(globalThis, "fetch").mockImplementation(() => Promise.resolve(json({})));
    await api.fetchPeers("p", "ovn 1");
    await api.createPeer("p", "ovn1", { name: "to2", description: "", target_project: "q", target_network: "ovn2" });
    await api.updatePeer("p", "ovn1", "to 2", { description: "d", config: {} });
    await api.deletePeer("p", "ovn1", "to2");
    const calls = f.mock.calls.map(([url, init]) => [url, (init as RequestInit).method, (init as RequestInit).body ?? null]);
    expect(calls).toEqual([
      ["/api/networks/p/ovn%201/peers", "GET", null],
      ["/api/networks/p/ovn1/peers", "POST", '{"name":"to2","description":"","target_project":"q","target_network":"ovn2"}'],
      ["/api/networks/p/ovn1/peers/to%202", "PUT", '{"description":"d","config":{}}'],
      ["/api/networks/p/ovn1/peers/to2", "DELETE", null],
    ]);
  });
});

describe("volume copy and move (Phase 10a)", () => {
  it("send the right method, URL and body", async () => {
    const f = vi.spyOn(globalThis, "fetch").mockImplementation(() => Promise.resolve(json({})));
    await api.copyVolume("p", "local", "v1", { name: "v2", pool: "remote", volume_only: false }, "m1");
    await api.moveVolume("p", "remote", "v1", { project: "q" });
    const calls = f.mock.calls.map(([url, init]) => [url, (init as RequestInit).method, (init as RequestInit).body ?? null]);
    expect(calls).toEqual([
      ["/api/volumes/p/local/v1/copy?target=m1", "POST", '{"name":"v2","pool":"remote","volume_only":false}'],
      ["/api/volumes/p/remote/v1/move", "POST", '{"project":"q"}'],
    ]);
  });
});
