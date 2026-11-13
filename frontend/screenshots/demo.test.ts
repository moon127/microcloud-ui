import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import type { QueryResult } from "../src/api.ts";
import { HOSTS_VIEW, LIST, NOW, answer, query } from "./demo.ts";

const get = (path: string) => answer("GET", new URL(path, "http://demo"));

/** Metric names the pages on the screenshots query (`q("…")` and `name: "…"`). */
function metricNames(): string[] {
  const files = ["Overview.tsx", "MemberPage.tsx", "StoragePage.tsx", "CephPage.tsx", "NetworksPage.tsx", "instance/Graphs.tsx"];
  const names = new Set<string>();
  for (const f of files) {
    const src = readFileSync(`src/pages/${f}`, "utf8"); // vitest runs in frontend/
    for (const m of src.matchAll(/(?:q\(|name: )"((?:mcui|lxd|node|ceph)_[A-Za-z0-9_]+)"/g)) names.add(m[1]);
  }
  return [...names];
}

describe("demo cluster", () => {
  it("answers every metric the screenshot pages ask for", () => {
    const names = metricNames();
    expect(names.length).toBeGreaterThan(50);
    const empty = names.filter((name) => (query(new URLSearchParams({ name, range: "3600" })) as QueryResult).series.length === 0);
    expect(empty).toEqual([]);
  });

  it("filters by match and sums by labels", () => {
    const one = query(new URLSearchParams([["name", "node_cpu_seconds_total"], ["match", "member=node-2"], ["range", "3600"]]));
    expect(one.series).toHaveLength(7); // one per CPU mode
    expect(one.series.every((s) => s.labels.member === "node-2")).toBe(true);
    expect(one.end).toBe(NOW);
    const byMode = query(new URLSearchParams([["name", "node_cpu_seconds_total"], ["by", "mode"], ["range", "3600"], ["fn", "rate"]]));
    expect(byMode.fn).toBe("rate");
    expect(byMode.series.map((s) => s.labels)).toContainEqual({ mode: "idle" });
    const idle = byMode.series.find((s) => s.labels.mode === "idle")!;
    const oneIdle = one.series.find((s) => s.labels.mode === "idle")!;
    expect(idle.points[0][1]).toBeGreaterThan(oneIdle.points[0][1]); // three hosts summed
    expect(query(new URLSearchParams({ name: "unknown_metric" })).series).toEqual([]);
  });

  it("answers the API paths the pages use, with documentation addresses only", () => {
    for (const path of [
      "/api/auth/me",
      "/api/server",
      "/api/metrics/live",
      "/api/hosts",
      "/api/cluster/members",
      "/api/cluster/members/node-1/resources",
      "/api/projects",
      "/api/instances",
      "/api/instances/web/db-primary",
      "/api/storage-pools",
      "/api/networks?project=default",
      "/api/networks?project=default&target=node-1",
      "/api/profiles?project=default",
      "/api/images?project=default",
      "/api/images/remote/images",
      "/api/ceph",
      "/api/ceph/rbd?metric=write_bytes&range=3600",
      "/api/ovn",
      "/api/metrics/top?metric=memory&range=3600",
      "/api/metrics/network-traffic?range=3600",
      "/api/operations",
      // LXD 6 (MicroCloud 3.4) pages
      "/api/placement-groups?project=default",
      "/api/image-registries",
      "/api/networks/default/default",
      "/api/networks/default/default/state",
      "/api/networks/default/default/load-balancers",
      "/api/networks/default/default/load-balancer-pools",
      "/api/networks/default/default/load-balancer-pools/front/state",
    ]) {
      expect(get(path), path).toBeDefined();
    }
    expect(get("/api/nope")).toBeUndefined();
    expect(get("/api/instances/web/nope")).toBeUndefined();
    expect(answer("POST", new URL("http://demo/api/instances"))).toEqual({});
    const all = JSON.stringify([
      HOSTS_VIEW,
      LIST,
      ...["/api/server", "/api/ceph", "/api/ovn", "/api/cluster/members", "/api/image-registries"].map(get),
      ...["", "/state", "/load-balancers", "/load-balancer-pools/front/state"].map((s) => get(`/api/networks/default/default${s}`)),
    ]);
    const ips = all.match(/\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b/g) ?? [];
    // 192.0.2.0/24 and 198.51.100.0/24 are documentation ranges; 10.x are private instance addresses
    expect(ips.filter((a) => !/^(192\.0\.2|198\.51\.100|10)\./.test(a))).toEqual([]);
  });
});
