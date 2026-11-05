import { describe, expect, it } from "vitest";
import { formatBytes, formatDuration, formatNumber, formatPercent, formatRate } from "./format";

describe("format", () => {
  it("formats bytes", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(1023)).toBe("1023 B");
    expect(formatBytes(1536)).toBe("1.5 KiB");
    expect(formatBytes(500 * 1024 * 1024)).toBe("500 MiB");
    expect(formatBytes(-2048)).toBe("-2.0 KiB");
    expect(formatBytes(2 ** 60)).toBe("1024 PiB"); // largest unit caps
    expect(formatBytes(null)).toBe("–");
    expect(formatBytes(undefined)).toBe("–");
    expect(formatBytes(NaN)).toBe("–");
  });

  it("formats rates and percentages", () => {
    expect(formatRate(1024)).toBe("1.0 KiB/s");
    expect(formatRate(null)).toBe("–");
    expect(formatPercent(23.333)).toBe("23.3%");
    expect(formatPercent(null)).toBe("–");
    expect(formatPercent(Infinity)).toBe("–");
  });

  it("formats durations", () => {
    expect(formatDuration(42)).toBe("0m 42s");
    expect(formatDuration(3 * 3600 + 5 * 60)).toBe("3h 5m");
    expect(formatDuration(2 * 86400 + 3600)).toBe("2d 1h");
    expect(formatDuration(undefined)).toBe("–");
  });

  it("formats plain numbers compactly", () => {
    expect(formatNumber(0)).toBe("0");
    expect(formatNumber(0.123)).toBe("0.12");
    expect(formatNumber(42)).toBe("42");
    expect(formatNumber(1500)).toBe("1.5k");
    expect(formatNumber(2.5e6)).toBe("2.5M");
    expect(formatNumber(-3e9)).toBe("-3.0G");
    expect(formatNumber(null)).toBe("–");
  });
});
