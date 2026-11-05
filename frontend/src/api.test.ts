import { describe, expect, it, vi } from "vitest";
import { fetchHealth } from "./api";

describe("fetchHealth", () => {
  it("returns parsed health on 200", async () => {
    const f = vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: "ok", version: "1" })));
    await expect(fetchHealth(f)).resolves.toEqual({ status: "ok", version: "1" });
    expect(f).toHaveBeenCalledWith("/api/health");
  });

  it("throws on non-2xx", async () => {
    const f = vi.fn().mockResolvedValue(new Response("", { status: 503 }));
    await expect(fetchHealth(f)).rejects.toThrow("HTTP 503");
  });

  it("uses global fetch by default", async () => {
    const spy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({ status: "ok", version: "2" })));
    await expect(fetchHealth()).resolves.toMatchObject({ version: "2" });
    spy.mockRestore();
  });
});
