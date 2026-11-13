import { describe, expect, it } from "vitest";
import type { Instance, Member } from "../api";
import { evacuationRisks, liveMigrates, membersOut, mixedCpus, restoreRisks, votersLeft } from "./evacuation";

const m = (name: string, status: string, roles: string[]): Member => ({
  name, url: "", status, roles, failure_domain: "default", message: "", architecture: "x86_64",
});
const vm = (name: string, location: string, over: Partial<Instance> = {}): Instance => ({
  project: "default", name, type: "virtual-machine", status: "Running", location, description: "", nics: {}, stateful: true, ...over,
});
// the test cloud on 2026-11-13 (docs/LIVE-TESTS.md C2a): node-1 evacuated and demoted
const members = [m("node-1", "Evacuated", []), m("node-2", "Online", ["database-voter"]), m("node-3", "Online", ["database-leader"])];
const cpus = { "node-1": "Xeon D-1518", "node-2": "Xeon D-1518", "node-3": "Xeon D-2123IT" };

describe("evacuation risks", () => {
  it("finds members already out and the voters that would be left", () => {
    expect(membersOut(members, "node-2").map((x) => x.name)).toEqual(["node-1"]);
    expect(votersLeft(members, "node-2").map((x) => x.name)).toEqual(["node-3"]);
    expect(membersOut(members, "node-1")).toHaveLength(0);
  });

  it("knows which VMs live-migrate in each mode", () => {
    expect(liveMigrates(vm("a", "node-2"), "")).toBe(true);
    expect(liveMigrates(vm("a", "node-2", { evacuate: "auto" }), "")).toBe(true);
    expect(liveMigrates(vm("a", "node-2", { evacuate: "migrate" }), "")).toBe(false);
    expect(liveMigrates(vm("a", "node-2"), "migrate")).toBe(false);
    expect(liveMigrates(vm("a", "node-2", { evacuate: "migrate" }), "live-migrate")).toBe(true);
    expect(liveMigrates(vm("a", "node-2", { stateful: false }), "")).toBe(false);
    expect(liveMigrates(vm("a", "node-2", { status: "Stopped" }), "")).toBe(false);
    expect(liveMigrates(vm("a", "node-2", { type: "container" }), "")).toBe(false);
  });

  it("only cares about CPUs when they differ", () => {
    expect(mixedCpus(cpus)).toBe(true);
    expect(mixedCpus({ a: "x", b: "x", c: "" })).toBe(false);
    expect(evacuationRisks([vm("cos", "node-2"), vm("pod", "node-3")], "node-2", "").map((i) => i.name)).toEqual(["cos"]);
  });

  it("finds VMs a restore would try to live-migrate back from another CPU", () => {
    const back = [
      vm("minecraft", "node-3", { evacuated_from: "node-2" }), // newer CPU: fails
      vm("cold", "node-3", { evacuated_from: "node-2", status: "Stopped" }),
      vm("same", "node-1", { evacuated_from: "node-2" }), // same CPU: fine
      vm("home", "node-2", { evacuated_from: "node-2" }),
    ];
    expect(restoreRisks(back, "node-2", cpus).map((i) => i.name)).toEqual(["minecraft"]);
  });
});
