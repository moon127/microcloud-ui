// Invented demo cluster for the README screenshots (D59): three hosts on
// documentation addresses (192.0.2.0/24, 2001:db8::/32), made-up instances, and
// smooth, deterministic metric history. Typed against the real API so it can't drift.
import type {
  CephState,
  Host,
  Instance,
  InstanceFull,
  InstanceLive,
  Labels,
  LiveMetrics,
  LocalImage,
  Member,
  MemberLive,
  Network,
  NetworkTraffic,
  OvnNetwork,
  OvnUplink,
  Point,
  Profile,
  Project,
  QueryResult,
  RemoteImage,
  RbdRow,
  Resources,
  ServerInfo,
  Series,
  StoragePool,
  TopResult,
} from "../src/api.ts";

export const NOW = Date.UTC(2026, 10, 11, 14, 0, 0) / 1000;
const GIB = 1024 ** 3;
const HOSTS = ["node-1", "node-2", "node-3"];
const ip = (i: number) => `192.0.2.${11 + i}`;

// --- deterministic noise ---------------------------------------------------------

function hash(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return h >>> 0;
}

/** A value around ``base`` that drifts smoothly over the day, plus a little jitter. */
function wave(key: string, base: number, wobble: number, t: number): number {
  const h = hash(key);
  const phase = (h % 1000) / 1000;
  const day = Math.sin((t / 86400 + phase) * 2 * Math.PI) * 0.6;
  const hour = Math.sin((t / 10800 + phase * 7) * 2 * Math.PI) * 0.12;
  const jitter = (((hash(`${key}:${Math.floor(t / 60)}`) % 1000) / 1000) - 0.5) * 0.12;
  return Math.max(0, base * (1 + wobble * (day + hour + jitter)));
}

// --- the cluster ------------------------------------------------------------------

interface Demo {
  project: string;
  name: string;
  type: "container" | "virtual-machine";
  member: string;
  status: "Running" | "Stopped";
  cpus: number;
  memory: number; // limit, bytes
  cpu: number; // typical busy %, of its CPUs
  mem: number; // typical used fraction
  net: number; // typical rx bytes/s
  disk: number; // typical write bytes/s
  description: string;
}

const I = (
  project: string,
  name: string,
  type: Demo["type"],
  member: string,
  cpus: number,
  memGib: number,
  cpu: number,
  mem: number,
  net: number,
  disk: number,
  description: string,
  status: Demo["status"] = "Running",
): Demo => ({ project, name, type, member, status, cpus, memory: memGib * GIB, cpu, mem, net, disk, description });

export const INSTANCES: Demo[] = [
  I("web", "web-1", "container", "node-1", 2, 4, 38, 0.55, 2.4e6, 1.2e5, "nginx + app"),
  I("web", "web-2", "container", "node-2", 2, 4, 31, 0.52, 2.1e6, 1.0e5, "nginx + app"),
  I("web", "db-primary", "virtual-machine", "node-3", 4, 16, 46, 0.71, 9e5, 3.1e6, "PostgreSQL 17 primary"),
  I("web", "db-replica", "virtual-machine", "node-1", 4, 16, 12, 0.64, 7e5, 1.4e6, "PostgreSQL 17 streaming replica"),
  I("web", "cache", "container", "node-2", 1, 2, 9, 0.8, 1.2e6, 2e4, "Valkey"),
  I("default", "dns-1", "container", "node-1", 1, 1, 2, 0.3, 4e4, 3e3, "unbound"),
  I("default", "monitoring", "container", "node-2", 2, 4, 17, 0.62, 3e5, 4.5e5, "Grafana + Loki"),
  I("default", "build-runner", "virtual-machine", "node-3", 8, 16, 63, 0.48, 6e6, 8e6, "CI runner"),
  I("lab", "sandbox", "container", "node-3", 2, 2, 4, 0.25, 1e4, 2e3, "scratch space"),
  I("lab", "k8s-test", "virtual-machine", "node-2", 4, 8, 0, 0, 0, 0, "k3s experiments", "Stopped"),
];
const running = INSTANCES.filter((i) => i.status === "Running");

export const SERVER: ServerInfo = {
  version: "5.21.3",
  clustered: true,
  server_name: "node-1",
  auth: "trusted",
  auth_method: "tls",
  endpoint: "https://192.0.2.11:8443",
  features: { metrics: true, storage_buckets: true, network_peers: true, network_acls: true, vga_console: true },
};

export const MEMBERS: Member[] = HOSTS.map((name, i) => ({
  name,
  url: `https://${ip(i)}:8443`,
  status: "Online",
  roles: i === 0 ? ["database-leader", "database", "ovn-chassis"] : ["database", "ovn-chassis"],
  failure_domain: "default",
  message: "Fully operational",
  architecture: "x86_64",
}));

export const PROJECTS: Project[] = [
  { name: "default", description: "Default LXD project", config: { "features.images": "true", "features.profiles": "true" }, used_by: [] },
  { name: "web", description: "Production web stack", config: { "features.networks": "true", "limits.cpu": "32" }, used_by: [] },
  { name: "lab", description: "Experiments", config: { "limits.instances": "10" }, used_by: [] },
];

export const LIST: Instance[] = INSTANCES.map((d, n) => ({
  project: d.project,
  name: d.name,
  type: d.type,
  status: d.status,
  location: d.member,
  description: d.description,
  profiles: ["default"],
  nics: (d.status === "Running" ? { eth0: `10.${d.project === "web" ? 20 : 10}.0.${10 + n}` } : {}) as Record<string, string>,
}));

export function instanceFull(project: string, name: string): InstanceFull | undefined {
  const d = INSTANCES.find((i) => i.project === project && i.name === name);
  if (!d) return undefined;
  return {
    project,
    name,
    type: d.type,
    status: d.status,
    location: d.member,
    description: d.description,
    architecture: "x86_64",
    ephemeral: false,
    stateful: false,
    profiles: ["default"],
    config: { "limits.cpu": String(d.cpus), "limits.memory": `${d.memory / GIB}GiB`, "image.os": "Ubuntu", "image.release": "noble" },
    devices: {},
    expanded_config: { "limits.cpu": String(d.cpus), "limits.memory": `${d.memory / GIB}GiB` },
    expanded_devices: { root: { type: "disk", path: "/", pool: "remote" }, eth0: { type: "nic", network: "default", name: "eth0" } },
    created_at: "2026-09-02T10:14:00Z",
    last_used_at: "2026-11-10T07:00:00Z",
    snapshots: [
      { name: "nightly-2026-11-10", created_at: "2026-11-10T02:00:00Z", expires_at: "2026-11-17T02:00:00Z", stateful: false },
      { name: "nightly-2026-11-11", created_at: "2026-11-11T02:00:00Z", expires_at: "2026-11-18T02:00:00Z", stateful: false },
    ],
  };
}

export const RESOURCES: Resources = {
  cpu: { total: 16, architecture: "x86_64", sockets: [{ name: "AMD EPYC 4344P 8-Core Processor", vendor: "AuthenticAMD" }] },
  memory: { total: 64 * GIB },
  gpu: { total: 0, cards: [] },
  network: {
    cards: [
      { product: "Ethernet Controller X710 for 10GbE SFP+", driver: "i40e", ports: [{ id: "enp1s0f0", link_detected: true, link_speed: 10000 }, { id: "enp1s0f1", link_detected: true, link_speed: 10000 }] },
      { product: "I210 Gigabit Network Connection", driver: "igb", ports: [{ id: "eno1", link_detected: true, link_speed: 1000 }] },
    ],
  },
  storage: {
    disks: [
      { id: "nvme0n1", model: "Samsung SSD 990 PRO 2TB", type: "nvme", size: 2000398934016 },
      { id: "nvme1n1", model: "Samsung SSD 990 PRO 2TB", type: "nvme", size: 2000398934016 },
      { id: "sda", model: "Micron 5400 480GB", type: "scsi", size: 480103981056 },
    ],
  },
  system: { vendor: "Supermicro", product: "AS-1015A-MT" },
};

export const PROFILES: Profile[] = [
  { name: "default", description: "Default LXD profile", config: {}, devices: { root: { type: "disk", path: "/", pool: "remote" }, eth0: { type: "nic", network: "default", name: "eth0" } }, used_by: [], project: "default" },
  { name: "small", description: "1 CPU, 1 GiB", config: { "limits.cpu": "1", "limits.memory": "1GiB" }, devices: {}, used_by: [], project: "default" },
  { name: "cloud-init-ssh", description: "Adds the team's SSH keys", config: { "cloud-init.user-data": "#cloud-config\n" }, devices: {}, used_by: [], project: "default" },
];

export const NETWORKS: Network[] = [
  { name: "UPLINK", type: "physical", managed: true, description: "Upstream VLAN 20", status: "Created", config: { "ipv4.gateway": "192.0.2.1/24", "ipv4.ovn.ranges": "192.0.2.100-192.0.2.149" }, used_by: 2, locations: HOSTS },
  { name: "default", type: "ovn", managed: true, description: "Default OVN network", status: "Created", config: { network: "UPLINK", "ipv4.address": "10.10.0.1/24", "ipv4.nat": "true", "ipv6.address": "2001:db8:10::1/64" }, used_by: 6, locations: HOSTS },
  { name: "lxdfan0", type: "bridge", managed: false, description: "", status: "", config: {}, used_by: 0 },
  { name: "enp1s0f0", type: "physical", managed: false, description: "", status: "", config: {}, used_by: 0 },
];

export const POOLS: StoragePool[] = [
  { name: "remote", driver: "ceph", status: "Created", description: "Distributed storage on Ceph", config: { "ceph.osd.pool_name": "lxd_remote", "ceph.cluster_name": "ceph" }, used_by: 21, locations: HOSTS },
  { name: "remote-fs", driver: "cephfs", status: "Created", description: "Shared filesystems", config: { source: "lxd_cephfs" }, used_by: 2, locations: HOSTS },
  { name: "local", driver: "zfs", status: "Created", description: "Local NVMe storage", config: { source: "/dev/nvme1n1", "zfs.pool_name": "local" }, used_by: 4, locations: HOSTS },
];

export const IMAGES: LocalImage[] = [
  ["ubuntu/24.04", "Ubuntu noble amd64 (20261101_07:42)", "container", 132e6],
  ["ubuntu/24.04/vm", "Ubuntu noble amd64 (20261101_07:42)", "virtual-machine", 312e6],
  ["debian/13", "Debian trixie amd64 (20261101_05:24)", "container", 98e6],
  ["alpine/3.22", "Alpine 3.22 amd64 (20261101_13:00)", "container", 3.1e6],
].map(([alias, description, type, size], n) => ({
  fingerprint: `${(hash(String(alias)) * 2654435761).toString(16).padStart(8, "0")}${"a1b2c3d4e5f6".repeat(5)}`.slice(0, 64),
  aliases: [String(alias)],
  description: String(description),
  type: String(type),
  architecture: "x86_64",
  size: Number(size),
  created_at: "2026-11-01T00:00:00Z",
  uploaded_at: `2026-11-0${n + 2}T09:00:00Z`,
  last_used_at: "2026-11-10T07:00:00Z",
  cached: n > 1,
  public: false,
  auto_update: true,
  properties: { os: String(alias).split("/")[0], release: String(alias).split("/")[1] },
  project: "default",
}));

const remote = (os: string, release: string, title: string, variant = "default"): RemoteImage => ({
  alias: `${os.toLowerCase()}/${release}${variant === "default" ? "" : `/${variant}`}`,
  aliases: [`${os.toLowerCase()}/${release}`],
  os,
  release,
  release_title: title,
  variant,
  arch: "amd64",
  container: true,
  vm: os !== "Alpine",
  version: "20261110_07:42",
});
export const REMOTE_IMAGES: RemoteImage[] = [
  remote("Ubuntu", "24.04", "noble"),
  remote("Ubuntu", "24.04", "noble", "cloud"),
  remote("Ubuntu", "22.04", "jammy"),
  remote("Debian", "13", "trixie"),
  remote("Debian", "12", "bookworm"),
  remote("Alpine", "3.22", "3.22"),
  remote("Fedora", "43", "43"),
  remote("Rockylinux", "10", "10"),
];

export const HOST_INTERFACES: Network[] = [
  { name: "enp1s0f0", type: "physical", managed: false, description: "", status: "", config: {}, used_by: 1 },
  { name: "enp1s0f1", type: "physical", managed: false, description: "", status: "", config: {}, used_by: 0 },
  { name: "eno1", type: "physical", managed: false, description: "", status: "", config: {}, used_by: 0 },
  { name: "br-int", type: "bridge", managed: false, description: "", status: "", config: {}, used_by: 0 },
];

// --- live and hosts ----------------------------------------------------------------

const used = (d: Demo) => d.memory * d.mem;

export const LIVE: LiveMetrics = {
  scraped_at: NOW,
  interval_seconds: 15,
  members: MEMBERS.map((m, i): MemberLive => ({
    ...m,
    scrape: { member: m.name, url: `https://${ip(i)}:8444`, port: 8444, ok: true, error: null, fell_back: false, samples: 2400 + i * 130 },
    daemon: { uptime_seconds: 1_640_000 + i * 86_000, goroutines: 610 + i * 37, warnings: i === 2 ? 1 : 0 },
    instances: INSTANCES.filter((d) => d.member === m.name).length,
  })),
  instances: INSTANCES.map((d): InstanceLive => {
    const on = d.status === "Running";
    return {
      member: d.member,
      project: d.project,
      name: d.name,
      type: d.type,
      cpu_percent: on ? wave(`cpu:${d.name}`, d.cpu, 0.3, NOW) : null,
      cpus: d.cpus,
      memory_total: on ? d.memory : null,
      memory_used: on ? used(d) : null,
      memory_rss: on ? used(d) * 0.7 : null,
      memory_cached: on ? used(d) * 0.2 : null,
      memory_swap: on ? 0 : null,
      oom_kills: on ? 0 : null,
      disk_read_bps: on ? d.disk * 0.3 : null,
      disk_write_bps: on ? d.disk : null,
      disk_read_iops: on ? d.disk / 16000 : null,
      disk_write_iops: on ? d.disk / 8000 : null,
      net_rx_bps: on ? d.net : null,
      net_tx_bps: on ? d.net * 0.6 : null,
      procs: on ? (d.type === "container" ? 40 : 180) + (hash(d.name) % 60) : null,
      rootfs_size: on ? 20 * GIB : null,
      rootfs_used: on ? (4 + (hash(d.name) % 10)) * GIB : null,
    };
  }),
};

const hostCpu = [21, 34, 58];
export const HOSTS_VIEW: { node_exporter: boolean; hosts: Host[] } = {
  node_exporter: true,
  hosts: HOSTS.map((name, i): Host => {
    const mine = INSTANCES.filter((d) => d.member === name);
    const on = mine.filter((d) => d.status === "Running");
    const memUsed = [18, 27, 41][i] * GIB;
    return {
      name,
      status: "Online",
      roles: MEMBERS[i].roles,
      cpus: 16,
      load1: (hostCpu[i] / 100) * 16 * 1.1,
      load5: (hostCpu[i] / 100) * 16,
      uptime: 3_200_000 + i * 90_000,
      memory_total: 64 * GIB,
      memory_free: 64 * GIB - memUsed - 14 * GIB,
      memory_buffered: GIB,
      swap_total: 8 * GIB,
      swap_free: 8 * GIB - [0.1, 0.2, 0.6][i] * GIB,
      instances: mine.length,
      instances_running: on.length,
      instances_cpu: on.reduce((s, d) => s + (d.cpu / 100) * d.cpus, 0),
      instances_memory: on.reduce((s, d) => s + used(d), 0),
      allocated_cpus: on.reduce((s, d) => s + d.cpus, 0),
      allocated_memory: on.reduce((s, d) => s + d.memory, 0),
      unlimited: 0,
      net_rx_bps: [3.1e6, 2.6e6, 6.4e6][i],
      net_tx_bps: [1.9e6, 1.5e6, 4.2e6][i],
      node:
        i === 2
          ? { url: `http://${ip(i)}:9100`, ok: false, error: "not reachable (not installed?)", absent: true }
          : {
              url: `https://${ip(i)}:9100`,
              ok: true,
              error: null,
              absent: false,
              memory_total: 64 * GIB,
              memory_used: memUsed,
              memory_cache: 14 * GIB,
              zfs_arc: 4 * GIB,
              swap_total: 8 * GIB,
              swap_used: [0.1, 0.2][i] * GIB,
              rootfs_size: 98 * GIB,
              rootfs_used: [41, 63][i] * GIB,
              timex_sync: 1,
              timex_offset: 0.0002,
              cpu_percent: hostCpu[i],
              cpu_iowait_percent: [0.8, 6.2][i],
              cpu_steal_percent: 0,
              disk_busy_percent: [7, 22][i],
              disk_busiest: "nvme0n1",
              disk_read_bps: 2.1e6,
              disk_write_bps: 6.8e6,
              pressure_cpu: [0.9, 2.4][i],
              pressure_memory: 0,
              pressure_io: [0.4, 3.1][i],
            },
    };
  }),
};

export function top(metric: string): TopResult {
  const on = running.slice();
  const value: Record<string, (d: Demo) => number> = {
    cpu: (d) => d.cpu,
    memory: used,
    net: (d) => d.net * 1.6,
    disk: (d) => d.disk * 1.3,
  };
  const f = value[metric] ?? value.cpu;
  const unit = metric === "cpu" ? "percent" : metric === "memory" ? "bytes" : "bytes_per_second";
  return {
    metric: metric as TopResult["metric"],
    unit,
    rows: on.sort((a, b) => f(b) - f(a)).slice(0, 5).map((d) => ({ project: d.project, name: d.name, value: f(d) })),
  };
}

// --- Ceph and OVN --------------------------------------------------------------------

const CEPH_POOLS = [
  { id: 1, name: ".mgr", stored: 1.9e6, objects: 2, pgs: 1, pct: 0.0001 },
  { id: 2, name: "lxd_remote", stored: 1.31e12, objects: 341_208, pgs: 128, pct: 0.41 },
  { id: 3, name: "lxd_cephfs_meta", stored: 2.1e8, objects: 1_904, pgs: 16, pct: 0.0001 },
  { id: 4, name: "lxd_cephfs_data", stored: 1.84e11, objects: 51_377, pgs: 32, pct: 0.09 },
];
const OSDS = [0, 1, 2, 3, 4, 5];

export const CEPH: CephState = {
  enabled: true,
  ok: true,
  mgr: "http://192.0.2.12:9283",
  scraped_at: NOW,
  health: { status: "HEALTH_OK", checks: [] },
  version: "19.2.3 squid",
  mons: HOSTS.map((name) => ({ name, in_quorum: true })),
  mgrs: HOSTS.map((name, i) => ({ name: `${name}.mgr`, active: i === 1 })),
  osds: OSDS.map((id) => ({
    id,
    host: HOSTS[Math.floor(id / 2)],
    device_class: "nvme",
    up: true,
    in: true,
    pgs: 88 + ((id * 7) % 11),
    size: 2 * 1.819e12,
    used: (0.33 + ((id * 13) % 9) / 100) * 2 * 1.819e12,
    apply_latency_ms: 1 + (id % 3),
    commit_latency_ms: 1 + (id % 3),
  })),
  pgs: { total: 177, states: { active: 177, clean: 177 } },
  capacity: { total: 12 * 1.819e12, used_raw: 4.48e12 },
  pools: CEPH_POOLS.map((p) => ({
    id: p.id,
    name: p.name,
    type: "replicated",
    description: "replica:3",
    stored: p.stored,
    max_avail: 2.36e12,
    percent_used: p.pct,
    objects: p.objects,
    pgs: p.pgs,
  })),
};

export const RBD: RbdRow[] = [
  { pool: "lxd_remote", image: "virtual-machine_web_db-primary.block", value: 2.9e6, owner: { kind: "instance", type: "virtual-machine", project: "web", name: "db-primary", block: true } },
  { pool: "lxd_remote", image: "virtual-machine_build-runner.block", value: 2.2e6, owner: { kind: "instance", type: "virtual-machine", project: "default", name: "build-runner", block: true } },
  { pool: "lxd_remote", image: "virtual-machine_web_db-replica.block", value: 1.3e6, owner: { kind: "instance", type: "virtual-machine", project: "web", name: "db-replica", block: true } },
  { pool: "lxd_remote", image: "container_monitoring", value: 4.1e5, owner: { kind: "instance", type: "container", project: "default", name: "monitoring" } },
  { pool: "lxd_remote", image: "custom_web_uploads", value: 1.7e5, owner: { kind: "volume", type: "custom", project: "web", name: "uploads" } },
];

const ovn = (name: string, project: string, n: number, over: Partial<OvnNetwork> = {}): OvnNetwork => ({
  name,
  project,
  description: "",
  status: "Created",
  uplink: "UPLINK",
  ipv4: `10.${10 + n * 10}.0.1/24`,
  ipv6: `2001:db8:${10 + n * 10}::1/64`,
  ipv4_nat: true,
  ipv6_nat: false,
  router_ipv4: `192.0.2.${100 + n}`,
  router_ipv6: "",
  acls: n === 1 ? ["web-ingress"] : [],
  used_by: [6, 5, 1][n],
  chassis: HOSTS[n],
  state_error: null,
  forwards: n === 1 ? [{}, {}] : [],
  load_balancers: n === 1 ? [{}] : [],
  peers: [],
  allocations: { instance: [6, 5, 1][n], network: 1 },
  ...over,
});

export const OVN: { networks: OvnNetwork[]; uplinks: OvnUplink[] } = {
  networks: [ovn("default", "default", 0), ovn("web-net", "web", 1), ovn("lab-net", "default", 2, { description: "Isolated lab" })],
  uplinks: [
    {
      name: "UPLINK",
      status: "Created",
      ipv4_gateway: "192.0.2.1/24",
      ipv6_gateway: "2001:db8::1/64",
      ipv4_ranges: "192.0.2.100-192.0.2.149",
      ipv6_ranges: "",
      ipv4_routes: "198.51.100.0/26",
      dns: "192.0.2.1",
      bgp_peers: ["edge-router"],
      networks: ["default", "web-net", "lab-net"],
      ipv4_used: 3,
      ipv4_size: 50,
    },
  ],
};

export function traffic(range: number): NetworkTraffic {
  const series = (dir: string, scale: number): Series[] =>
    ["default", "web-net"].map((network) => ({ labels: { network }, points: points(`${dir}:${network}`, (network === "default" ? 3e6 : 4.5e6) * scale, 0.5, range) }));
  return { start: NOW - range, end: NOW, resolution: 0, rx: series("rx", 1), tx: series("tx", 0.6) };
}

// --- metric history ------------------------------------------------------------------

function points(key: string, base: number, wobble: number, range: number): Point[] {
  const n = 120;
  const step = Math.max(15, Math.floor(range / n));
  const out: Point[] = [];
  for (let t = NOW - range; t <= NOW; t += step) out.push([t, wave(key, base, wobble, t)]);
  return out;
}

interface Spec {
  sets: Labels[];
  base: (l: Labels) => number;
  wobble?: number;
}

const inst = (d: Demo, extra: Labels = {}): Labels => ({ member: d.member, project: d.project, name: d.name, ...extra });
const perInstance = (f: (d: Demo) => number, extra: Labels = {}, wobble = 0.3): Spec => ({
  sets: running.map((d) => inst(d, extra)),
  base: (l) => f(running.find((d) => d.name === l.name)!),
  wobble,
});
const perHost = (f: (i: number) => number, wobble = 0.2, extra: Labels[] = [{}]): Spec => ({
  sets: HOSTS.flatMap((member) => extra.map((e) => ({ member, ...e }))),
  base: (l) => f(HOSTS.indexOf(l.member)),
  wobble,
});
const cpuModes: Record<string, number> = { idle: 0.62, user: 0.24, system: 0.08, iowait: 0.03, softirq: 0.02, nice: 0.01, steal: 0 };
const hostBusy = (i: number) => hostCpu[i] / 30; // scales the per-mode split around the demo's CPU %

// size and typical used fraction per mountpoint
const FS: Record<string, [number, number]> = { "/": [98 * GIB, 0.42], "/boot": [2 * GIB, 0.11], "/boot/efi": [1 * GIB, 0.006] };
const MOUNTS: Labels[] = HOSTS.flatMap((member) => Object.keys(FS).map((mountpoint) => ({ member, mountpoint })));

const SPECS: Record<string, Spec> = {
  mcui_instance_cpu_busy_seconds_total: perInstance((d) => (d.cpu / 100) * d.cpus),
  mcui_instance_cpus: perInstance((d) => d.cpus, {}, 0),
  mcui_instance_memory_used_bytes: perInstance(used, {}, 0.08),
  lxd_memory_RSS_bytes: perInstance((d) => used(d) * 0.7, {}, 0.08),
  lxd_memory_Cached_bytes: perInstance((d) => used(d) * 0.2, {}, 0.1),
  lxd_memory_Swap_bytes: perInstance(() => 0, {}, 0),
  lxd_memory_MemTotal_bytes: perInstance((d) => d.memory, {}, 0),
  lxd_memory_OOM_kills_total: perInstance(() => 0, {}, 0),
  lxd_procs_total: perInstance((d) => (d.type === "container" ? 45 : 190), {}, 0.05),
  lxd_disk_read_bytes_total: perInstance((d) => d.disk * 0.3, { device: "root" }, 0.6),
  lxd_disk_written_bytes_total: perInstance((d) => d.disk, { device: "root" }, 0.6),
  lxd_disk_reads_completed_total: perInstance((d) => d.disk / 16000, { device: "root" }, 0.6),
  lxd_disk_writes_completed_total: perInstance((d) => d.disk / 8000, { device: "root" }, 0.6),
  lxd_network_receive_bytes_total: perInstance((d) => d.net, { device: "eth0" }, 0.5),
  lxd_network_transmit_bytes_total: perInstance((d) => d.net * 0.6, { device: "eth0" }, 0.5),
  lxd_network_receive_packets_total: perInstance((d) => d.net / 900, { device: "eth0" }, 0.5),
  lxd_network_transmit_packets_total: perInstance((d) => d.net / 1100, { device: "eth0" }, 0.5),
  lxd_network_receive_errs_total: perInstance(() => 0, { device: "eth0" }, 0),
  lxd_network_transmit_errs_total: perInstance(() => 0, { device: "eth0" }, 0),
  lxd_network_receive_drop_total: perInstance(() => 0, { device: "eth0" }, 0),
  lxd_network_transmit_drop_total: perInstance(() => 0, { device: "eth0" }, 0),
  lxd_filesystem_size_bytes: perInstance(() => 20 * GIB, { mountpoint: "/", device: "root" }, 0),
  lxd_filesystem_avail_bytes: perInstance((d) => (16 - (hash(d.name) % 10)) * GIB, { mountpoint: "/", device: "root" }, 0.01),
  lxd_api_requests_completed_total: {
    sets: HOSTS.flatMap((member) => [{ member, result: "succeeded" }, { member, result: "failed" }]),
    base: (l) => (l.result === "failed" ? 0.02 : [4.1, 2.2, 2.6][HOSTS.indexOf(l.member)]),
    wobble: 0.4,
  },
  lxd_go_goroutines: perHost((i) => 610 + i * 37, 0.05),
  lxd_api_requests_ongoing: perHost(() => 2, 0.5),
  lxd_warnings_total: perHost((i) => (i === 2 ? 1 : 0), 0),
  mcui_member_load1: perHost((i) => (hostCpu[i] / 100) * 16 * 1.1, 0.35),
  mcui_member_load5: perHost((i) => (hostCpu[i] / 100) * 16, 0.25),
  mcui_member_load15: perHost((i) => (hostCpu[i] / 100) * 16 * 0.95, 0.15),
  mcui_member_memory_total_bytes: perHost(() => 64 * GIB, 0),
  mcui_member_memory_free_bytes: perHost((i) => (64 - [18, 27, 41][i] - 14) * GIB, 0.1),
  mcui_member_memory_buffered_bytes: perHost(() => GIB, 0.05),
  mcui_member_cpus: perHost(() => 16, 0),
  mcui_host_network_receive_bytes_total: perHost((i) => [3.1e6, 2.6e6, 6.4e6][i], 0.5, [{ network: "enp1s0f0" }]),
  mcui_host_network_transmit_bytes_total: perHost((i) => [1.9e6, 1.5e6, 4.2e6][i], 0.5, [{ network: "enp1s0f0" }]),
  mcui_pool_space_total: {
    sets: HOSTS.flatMap((member) => [{ member, pool: "remote" }, { member, pool: "local" }]),
    base: (l) => (l.pool === "remote" ? 3.65e12 : 1.92e12),
    wobble: 0,
  },
  mcui_pool_space_used: {
    sets: HOSTS.flatMap((member) => [{ member, pool: "remote" }, { member, pool: "local" }]),
    base: (l) => (l.pool === "remote" ? 1.31e12 : [2.1e11, 3.4e11, 1.2e11][HOSTS.indexOf(l.member)]),
    wobble: 0.02,
  },
  node_cpu_seconds_total: {
    sets: HOSTS.flatMap((member) => Object.keys(cpuModes).map((mode) => ({ member, mode }))),
    base: (l) => {
      const i = HOSTS.indexOf(l.member);
      const f = l.mode === "idle" ? 1 - (1 - cpuModes.idle) * hostBusy(i) : cpuModes[l.mode] * hostBusy(i);
      return f * 16;
    },
    wobble: 0.3,
  },
  node_memory_MemTotal_bytes: perHost(() => 64 * GIB, 0),
  node_memory_MemAvailable_bytes: perHost((i) => (64 - [18, 27, 41][i]) * GIB, 0.06),
  node_memory_Cached_bytes: perHost(() => 13 * GIB, 0.05),
  node_memory_Buffers_bytes: perHost(() => GIB, 0.05),
  node_memory_SwapTotal_bytes: perHost(() => 8 * GIB, 0),
  node_memory_SwapFree_bytes: perHost((i) => (8 - [0.1, 0.2, 0.6][i]) * GIB, 0.01),
  node_zfs_arc_size: perHost(() => 4 * GIB, 0.1),
  node_filesystem_size_bytes: { sets: MOUNTS, base: (l) => FS[l.mountpoint][0], wobble: 0 },
  node_filesystem_avail_bytes: {
    sets: MOUNTS,
    base: (l) => FS[l.mountpoint][0] * (1 - FS[l.mountpoint][1] - HOSTS.indexOf(l.member) * 0.1),
    wobble: 0.005,
  },
  node_disk_read_bytes_total: perHost(() => 1.2e6, 0.7, [{ device: "nvme0n1" }, { device: "nvme1n1" }]),
  node_disk_written_bytes_total: perHost(() => 3.4e6, 0.7, [{ device: "nvme0n1" }, { device: "nvme1n1" }]),
  node_disk_io_time_seconds_total: perHost((i) => [0.07, 0.22, 0.1][i], 0.6, [{ device: "nvme0n1" }, { device: "nvme1n1" }]),
  node_pressure_cpu_waiting_seconds_total: perHost((i) => [0.009, 0.024, 0.01][i], 0.6),
  node_pressure_memory_waiting_seconds_total: perHost(() => 0.0002, 0.5),
  node_pressure_io_waiting_seconds_total: perHost((i) => [0.004, 0.031, 0.01][i], 0.6),
  ceph_cluster_total_bytes: { sets: [{}], base: () => 12 * 1.819e12, wobble: 0 },
  ceph_cluster_total_used_raw_bytes: { sets: [{}], base: () => 4.48e12, wobble: 0.01 },
  ceph_pool_stored: { sets: CEPH_POOLS.map((p) => ({ pool_id: String(p.id) })), base: (l) => CEPH_POOLS[Number(l.pool_id) - 1].stored, wobble: 0.01 },
  ceph_pool_rd_bytes: cephPools((p) => p.stored / 2e5),
  ceph_pool_wr_bytes: cephPools((p) => p.stored / 1.2e5),
  ceph_pool_rd: cephPools((p) => p.stored / 3e9),
  ceph_pool_wr: cephPools((p) => p.stored / 1e9),
  ceph_osd_apply_latency_ms: cephOsds(() => 2),
  ceph_osd_commit_latency_ms: cephOsds(() => 2),
  ceph_osd_op_w_latency_sum: cephOsds(() => 0.12),
  ceph_osd_op_w_latency_count: cephOsds(() => 60, 0),
  ceph_osd_op_r_latency_sum: cephOsds(() => 0.02),
  ceph_osd_op_r_latency_count: cephOsds(() => 25, 0),
};

function cephPools(f: (p: (typeof CEPH_POOLS)[number]) => number): Spec {
  const big = CEPH_POOLS.filter((p) => p.pgs > 1);
  return { sets: big.map((p) => ({ pool_id: String(p.id) })), base: (l) => f(CEPH_POOLS[Number(l.pool_id) - 1]), wobble: 0.6 };
}
function cephOsds(f: (id: number) => number, wobble = 0.4): Spec {
  return { sets: OSDS.map((id) => ({ ceph_daemon: `osd.${id}` })), base: (l) => f(Number(l.ceph_daemon.slice(4))), wobble };
}


export function query(params: URLSearchParams): QueryResult {
  const name = params.get("name") ?? "";
  const range = Number(params.get("range") ?? 3600);
  const match = Object.fromEntries(params.getAll("match").map((m) => m.split("=", 2) as [string, string]));
  const by = params.getAll("by");
  const spec = SPECS[name];
  const sets = (spec?.sets ?? []).filter((l) => Object.entries(match).every(([k, v]) => l[k] === v));
  let series: Series[] = sets.map((l) => ({ labels: l, points: points(`${name}:${JSON.stringify(l)}`, spec.base(l), spec.wobble ?? 0.3, range) }));
  if (by.length) {
    const groups = new Map<string, Series>();
    for (const s of series) {
      const labels = Object.fromEntries(by.map((k) => [k, s.labels[k] ?? ""]));
      const key = JSON.stringify(labels);
      const g = groups.get(key);
      if (!g) groups.set(key, { labels, points: s.points.map(([t, v]) => [t, v]) });
      else g.points = g.points.map(([t, v], i) => [t, v + (s.points[i]?.[1] ?? 0)]);
    }
    series = [...groups.values()];
  }
  return { name, kind: "gauge", resolution: 0, fn: (params.get("fn") as QueryResult["fn"]) ?? "value", start: NOW - range, end: NOW, series };
}

// --- the API --------------------------------------------------------------------------

/** The API answer for a request, or ``undefined`` for an unknown path. */
export function answer(method: string, url: URL): unknown {
  const p = url.pathname;
  const s = url.searchParams;
  if (method !== "GET") return {};
  const fixed: Record<string, unknown> = {
    "/api/auth/me": { user: { username: "admin", role: "admin" }, csrf: "demo" },
    "/api/health": { status: "ok", version: "0.1.0", lxd_configured: true },
    "/api/server": SERVER,
    "/api/metrics/live": LIVE,
    "/api/hosts": HOSTS_VIEW,
    "/api/cluster/members": MEMBERS,
    "/api/projects": PROJECTS,
    "/api/instances": LIST,
    "/api/storage-pools": POOLS,
    "/api/profiles": PROFILES,
    "/api/images": IMAGES,
    "/api/ceph": CEPH,
    "/api/ovn": OVN,
    "/api/operations": [],
    "/api/warnings": [],
    "/api/volumes": [],
    "/api/network-acls": [],
    "/api/network-allocations": [],
  };
  if (p in fixed) return fixed[p];
  if (p === "/api/networks") return s.get("target") ? HOST_INTERFACES : NETWORKS;
  if (p === "/api/metrics/query") return query(s);
  if (p === "/api/metrics/top") return top(s.get("metric") ?? "cpu");
  if (p === "/api/metrics/network-traffic") return traffic(Number(s.get("range") ?? 3600));
  if (p === "/api/ceph/rbd") return { metric: s.get("metric"), range: Number(s.get("range")), rows: RBD };
  if (p.startsWith("/api/images/remote/")) return REMOTE_IMAGES;
  let m = /^\/api\/cluster\/members\/[^/]+\/resources$/.exec(p);
  if (m) return RESOURCES;
  m = /^\/api\/instances\/([^/]+)\/([^/]+)$/.exec(p);
  if (m) return instanceFull(decodeURIComponent(m[1]), decodeURIComponent(m[2]));
  return undefined;
}
