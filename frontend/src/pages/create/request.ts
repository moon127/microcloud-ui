import type { NewInstance } from "../../api";

export interface Nic {
  name: string;
  network: string;
  ipv4: string;
}

export interface ExtraDisk {
  name: string;
  pool: string;
  volume: string;
  path: string;
}

export interface CreateForm {
  name: string;
  type: "container" | "virtual-machine";
  project: string;
  target: string;
  image: NewInstance["image"] | null;
  description: string;
  profiles: string[];
  cpu: string;
  memory: string;
  rootPool: string;
  rootSize: string;
  nics: Nic[];
  disks: ExtraDisk[];
  userData: string;
  vendorData: string;
  networkConfig: string;
  nesting: boolean;
  privileged: boolean;
  secureboot: boolean;
  autostart: boolean;
  start: boolean;
}

export const emptyForm = (project: string): CreateForm => ({
  name: "",
  type: "container",
  project,
  target: "",
  image: null,
  description: "",
  profiles: ["default"],
  cpu: "",
  memory: "",
  rootPool: "",
  rootSize: "",
  nics: [],
  disks: [],
  userData: "",
  vendorData: "",
  networkConfig: "",
  nesting: false,
  privileged: false,
  secureboot: true,
  autostart: false,
  start: true,
});

/** Reasons the form can't be submitted yet (empty = ready). */
export function problems(f: CreateForm): string[] {
  const out: string[] = [];
  if (!/^[A-Za-z][A-Za-z0-9-]{0,62}$/.test(f.name)) out.push("Name: letters, digits and dashes, starting with a letter");
  if (!f.image) out.push("Choose an image");
  const names = [...f.nics.map((n) => n.name), ...f.disks.map((d) => d.name)];
  if (names.some((n) => !n.trim())) out.push("Every NIC and disk needs a device name");
  if (new Set(names).size !== names.length || names.includes("root")) out.push("Device names must be unique (and not 'root')");
  if (f.nics.some((n) => !n.network)) out.push("Choose a network for every NIC");
  if (f.disks.some((d) => !d.pool || !d.volume || !d.path)) out.push("Every extra disk needs a pool, volume and path");
  return out;
}

/** The request body for POST /api/instances. */
export function buildRequest(f: CreateForm): NewInstance {
  const config: Record<string, string> = {};
  const set = (k: string, v: string | boolean, on = true) => {
    if (typeof v === "boolean" ? v === on : v.trim()) config[k] = typeof v === "boolean" ? String(v) : v.trim();
  };
  set("limits.cpu", f.cpu);
  set("limits.memory", f.memory);
  set("cloud-init.user-data", f.userData);
  set("cloud-init.vendor-data", f.vendorData);
  set("cloud-init.network-config", f.networkConfig);
  set("boot.autostart", f.autostart);
  if (f.type === "container") {
    set("security.nesting", f.nesting);
    set("security.privileged", f.privileged);
  } else {
    set("security.secureboot", f.secureboot, false);
  }

  const devices: Record<string, Record<string, string>> = {};
  if (f.rootPool || f.rootSize) {
    devices.root = { type: "disk", path: "/", ...(f.rootPool ? { pool: f.rootPool } : {}), ...(f.rootSize ? { size: f.rootSize } : {}) };
  }
  for (const n of f.nics) {
    devices[n.name] = { type: "nic", network: n.network, ...(n.ipv4.trim() ? { "ipv4.address": n.ipv4.trim() } : {}) };
  }
  for (const d of f.disks) {
    devices[d.name] = { type: "disk", pool: d.pool, source: d.volume, path: d.path };
  }
  return {
    name: f.name,
    type: f.type,
    project: f.project,
    ...(f.target ? { target: f.target } : {}),
    image: f.image ?? { remote: "local" },
    description: f.description,
    profiles: f.profiles,
    config,
    devices,
    start: f.start,
  };
}
