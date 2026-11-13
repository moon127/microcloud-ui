import { describe, expect, it } from "vitest";
import packageJson from "../package.json?raw";
import notices from "../THIRD_PARTY_NOTICES.md?raw";

const pkg = JSON.parse(packageJson) as { dependencies: Record<string, string> };

describe("THIRD_PARTY_NOTICES.md", () => {
  it("names the version of every exactly pinned runtime dependency (they ship in the bundle)", () => {
    const pinned = Object.entries(pkg.dependencies).filter(([, v]) => /^\d/.test(v));
    expect(pinned.length).toBeGreaterThan(4);
    const missing = pinned.filter(([, v]) => !notices.includes(v)).map(([n, v]) => `${n}@${v}`);
    expect(missing).toEqual([]);
  });
});
