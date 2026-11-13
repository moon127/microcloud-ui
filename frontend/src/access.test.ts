import { describe, expect, it } from "vitest";
import type { User } from "./api";
import { access, can, manageable, visibleProjects } from "./access";

const pat: User = { username: "pat", role: "project", grants: { web: "operator", lab: "viewer", ops: "admin" } };

describe("access (mirrors the server's policy, D81)", () => {
  it("global roles", () => {
    const admin: User = { username: "a", role: "admin" };
    const viewer: User = { username: "v", role: "viewer" };
    expect(can(admin, "any", "manage")).toBe(true);
    expect(can(viewer, "any", "read")).toBe(true);
    expect(can(viewer, "any", "operate")).toBe(false);
    expect(visibleProjects(viewer)).toBeNull();
    expect(access(viewer)).toMatchObject({ cluster: true, clusterAdmin: false });
    expect(access(admin).canManageSomewhere()).toBe(true);
    expect(access(viewer).canManageSomewhere()).toBe(false);
  });

  it("project users act through their grants only", () => {
    expect(can(pat, "web", "operate")).toBe(true);
    expect(can(pat, "web", "manage")).toBe(false);
    expect(can(pat, "lab", "read")).toBe(true);
    expect(can(pat, "lab", "operate")).toBe(false);
    expect(can(pat, "other", "read")).toBe(false);
    expect(can({ username: "x", role: "project" }, "web", "read")).toBe(false);
    expect(visibleProjects(pat)).toEqual(["lab", "ops", "web"]);
    expect(manageable(pat)).toEqual(["ops"]);
    const a = access(pat);
    expect(a.cluster).toBe(false);
    expect(a.canManageSomewhere()).toBe(true);
    expect(a.canManageSomewhere("web")).toBe(false);
    expect(access({ username: "y", role: "project", grants: { web: "viewer" } }).canManageSomewhere()).toBe(false);
  });
});
