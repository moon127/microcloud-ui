import type { Host } from "../api";

export const HOST_METRICS_DOC = "docs/HOST-METRICS.md";

/** Memory in use: node_exporter's (total - available) when there, else LXD's
 * total - free - buffers, which still counts page cache (an overestimate). */
export function hostMemory(h: Host): { used: number; total: number; estimated: boolean } | null {
  const n = h.node;
  if (n?.ok && n.memory_used != null && n.memory_total)
    return { used: n.memory_used, total: n.memory_total, estimated: false };
  if (h.memory_total == null || h.memory_free == null) return null;
  return {
    used: h.memory_total - h.memory_free - (h.memory_buffered ?? 0),
    total: h.memory_total,
    estimated: true,
  };
}
