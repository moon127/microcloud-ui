import type { StateAction } from "../../api";

/** Which lifecycle buttons make sense for a status. */
export function stateActions(status: string): StateAction[] {
  switch (status) {
    case "Running":
      return ["stop", "restart", "freeze"];
    case "Frozen":
      return ["unfreeze", "stop"];
    default:
      return ["start"];
  }
}

/** Coerce parsed YAML into LXD's string-only config map, or explain why not. */
export function asConfig(v: unknown): Record<string, string> | string {
  if (v === null || v === undefined) return {};
  if (typeof v !== "object" || Array.isArray(v)) return "config must be a mapping of keys to values";
  const out: Record<string, string> = {};
  for (const [k, x] of Object.entries(v as Record<string, unknown>)) {
    if (x !== null && typeof x === "object") return `${k}: values must be plain text, numbers or booleans`;
    out[k] = x === null || x === undefined ? "" : String(x);
  }
  return out;
}

/** Same for devices: a mapping of device name to its string config. */
export function asDevices(v: unknown): Record<string, Record<string, string>> | string {
  if (v === null || v === undefined) return {};
  if (typeof v !== "object" || Array.isArray(v)) return "devices must be a mapping of device names";
  const out: Record<string, Record<string, string>> = {};
  for (const [name, d] of Object.entries(v as Record<string, unknown>)) {
    const c = asConfig(d);
    if (typeof c === "string") return `${name}: ${c}`;
    if (!c.type) return `${name}: every device needs a type`;
    out[name] = c;
  }
  return out;
}
