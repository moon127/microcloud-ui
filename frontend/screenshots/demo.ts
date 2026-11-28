// Invented demo cluster for the README screenshots (D59): three hosts on
// documentation addresses (192.0.2.0/24, 2001:db8::/32), made-up instances, and
// smooth, deterministic metric history. Typed against the real API so it can't drift.
// It runs MicroCloud 3.4 LTS (LXD 6.10, Ceph Tentacle), so the LXD 6 features show;
// on MicroCloud 2.x (LXD 5.21) the same pages appear without them.
import type {
  AuditEntry,
  AlertRow,
  AlertSettingsView,
  Backup,
  HostDeviceSettings,
  HostEntry,
  HostFolder,
  HostInventory,
  CephState,
  Host,
  ImageRegistry,
  Instance,
  InstanceFull,
  InstanceLive,
  Labels,
  LbPool,
  LiveMetrics,
  LoadBalancer,
  LocalImage,
  Member,
  MemberLive,
  Network,
  NetworkDetail,
  NetworkPeer,
  NetworkState,
  NetworkTraffic,
  OvnNetwork,
  OvnUplink,
  PlacementGroup,
  Point,
  PoolTarget,
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
  User,
  Volume,
  VolumeDetail,
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
/** Set no limits (its cpus/memory are what it happens to use), so the Hosts table lists it. */
const unlimited = (d: Demo) => d.project === "lab" && d.name === "sandbox";
const limits = (d: Demo): Record<string, string> =>
  unlimited(d) ? {} : { "limits.cpu": String(d.cpus), "limits.memory": `${d.memory / GIB}GiB` };

export const SERVER: ServerInfo = {
  version: "6.10.1",
  clustered: true,
  server_name: "node-1",
  auth: "trusted",
  auth_method: "tls",
  endpoint: "https://192.0.2.11:8443",
  features: {
    clustering: true,
    metrics: true,
    vga_console: true,
    network_acls: true,
    network_forwards: true,
    network_load_balancers: true,
    network_peers: true,
    storage_buckets: true,
    cluster_evacuation: true,
    // LXD 6.x (MicroCloud 3.4); LXD 5.21 has none of these
    placement_groups: true,
    snapshot_volumes: true,
    load_balancer_pools: true,
    load_balancer_health_checks: true,
    image_registries: true,
    cephfs_path: true,
    evacuation_force: true,
    boot_mode: true,
    ovn_dhcp_ranges: true,
  },
};

export const MEMBERS: Member[] = HOSTS.map((name, i) => ({
  name,
  url: `https://${ip(i)}:8443`,
  status: "Online",
  roles: i === 0 ? ["database-leader"] : ["database-voter"],
  failure_domain: "default",
  message: "Fully operational",
  architecture: "x86_64",
}));

export const PROJECTS: Project[] = [
  { name: "default", description: "Default LXD project", config: { "features.images": "true", "features.profiles": "true" }, used_by: [] },
  {
    name: "web",
    description: "Production web stack",
    config: {
      "features.networks": "true",
      "limits.cpu": "32",
      restricted: "true",
      "restricted.snapshots": "allow",
      "restricted.backups": "allow",
    },
    used_by: [],
  },
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

/** Host paths and USB devices (D86): the CI runner's NFS cache (on every member) and a
 * hardware signing token on its member. */
const HOST_DEVICES: Record<string, Record<string, Record<string, string>>> = {
  "build-runner": {
    "ci-cache": { type: "disk", source: "/srv/nfs/ci-cache", path: "/mnt/ci-cache" },
    "signing-token": { type: "usb", vendorid: "1209", productid: "0001", serial: "HT-0042" },
  },
};

/** The USB devices of a member (invented: pid.codes test IDs). */
export const HOST_INVENTORY = (member: string): HostInventory => ({
  member,
  usb: [
    { vendor_id: "1209", product_id: "0001", vendor: "Example Labs", product: "Hardware token", serial: "HT-0042", busnum: 1, devnum: 4, storage: false, drivers: [] },
    { vendor_id: "1209", product_id: "0003", vendor: "Example Labs", product: "Flash Disk", serial: "FD-7781", busnum: 2, devnum: 2, storage: true, drivers: ["usb-storage"] },
    { vendor_id: "1209", product_id: "0002", vendor: "Example Labs", product: "Serial console adapter", serial: "", busnum: 1, devnum: 5, storage: false, drivers: ["cp210x"] },
  ],
  mounts: [
    { path: "/boot", fstype: "ext4", size: 2 * GIB },
    { path: "/srv/nfs", fstype: "nfs4", size: 8 * 1024 * GIB },
    { path: "/srv/san", fstype: "xfs", size: 2 * 1024 * GIB },
  ],
  browse: true,
  hidden: [],
  allowed: [],
  disks: [
    {
      id: "sdc",
      model: "Flash Disk",
      size: 64 * GIB,
      source: "/dev/disk/by-id/usb-Example_Flash_Disk_FD-7781-0:0",
      partitions: [{ id: "sdc1", size: 64 * GIB, source: "/dev/disk/by-id/usb-Example_Flash_Disk_FD-7781-0:0-part1" }],
    },
  ],
});

/** What the folder browser shows on a member (D87). */
const D = (name: string, extra: Partial<HostEntry> = {}): HostEntry => ({ name, type: "dir", size: 4096, mtime: 1794500000, ...extra });
const HOST_FOLDERS: Record<string, HostEntry[]> = {
  "/": ["bin", "boot", "etc", "home", "opt", "srv", "usr", "var"].map((n) => D(n)),
  "/srv": [D("nfs", { mount: "nfs4" }), D("san", { mount: "xfs" }), D("www")],
  "/srv/nfs": [D("ci-cache"), D("datasets"), D("media"), D("releases"), { name: "README.txt", type: "file", size: 2048, mtime: 1794500000 }],
};
export const hostFolder = (member: string, path: string): HostFolder => ({ member, path, entries: HOST_FOLDERS[path] ?? [] });

/** Alerts (D91): what the demo cluster has wrong now, and what cleared. */
const A = (id: number, rule: string, subject: string, severity: "warning" | "critical", title: string, detail: string, link: string, firedAgo: number, extra: Partial<AlertRow> = {}): AlertRow => ({
  id, key: `${rule}:${subject}`, rule, subject, severity, title, detail, link, project: null,
  fired_at: 1794400000 - firedAgo, last_seen: 1794400000, resolved_at: null, ack_by: null, ack_at: null, data: {}, ...extra,
});
export const ALERTS_ACTIVE: AlertRow[] = [
  A(6, "ceph_health", "OSD_NEARFULL", "warning", "An OSD is nearly full", "Ceph health check OSD_NEARFULL (HEALTH_WARN). Details: ceph health detail", "/ceph#osds", 1500),
  A(5, "local_pool", "node-2/local", "critical", "Storage pool local on node-2 is filling up", "Pool local on node-2 (92%; warning at 80%, critical at 90%)", "/storage/pools/local", 3600),
  A(4, "network_addresses", "default/default", "warning", "Network default is running out of addresses", "205 of 253 IPv4 addresses in 10.10.0.1/24 in use (81%; warning at 80%, critical at 95%)", "/networks/default/default", 7200, { ack_by: "admin" }),
  A(3, "instance_rootfs", "web/db-primary", "warning", "db-primary's root disk is filling up", "Root filesystem of web/db-primary (91%; warning at 90%, critical at 95%)", "/instances/web/db-primary", 86400, { project: "web" }),
];
export const ALERTS_HISTORY: AlertRow[] = [
  A(2, "member_status", "node-3", "warning", "node-3 is evacuated", "Evacuated: its instances run elsewhere until it's restored", "/members/node-3", 172800, { resolved_at: 1794400000 - 169200 }),
  A(1, "host_clock", "node-1", "warning", "node-1's clock isn't synchronised", "The kernel reports the clock unsynchronised (check chrony or systemd-timesyncd); Ceph and OVN need it", "/members/node-1", 259200, { resolved_at: 1794400000 - 258900 }),
];
const THRESHOLDS: Record<string, [number, number]> = {
  ceph_raw: [75, 85], ceph_pool: [80, 90], local_pool: [80, 90], host_fs: [85, 95], host_memory: [90, 95], instance_rootfs: [90, 95],
  project_limits: [90, 98], ovn_uplink: [80, 95], network_addresses: [80, 95], cert_days: [30, 7],
};
const RULE_INFO: [string, string, string | null, number][] = [
  ["ceph_health", "Ceph's own health checks (OSD nearfull, down, degraded, slow...)", null, 0],
  ["ceph_raw", "Raw Ceph capacity used (%)", "ceph_raw", 60],
  ["ceph_pool", "Ceph pools used, as Ceph counts it: copies and the fullest OSD (%)", "ceph_pool", 60],
  ["local_pool", "Local storage pools on each member (%)", "local_pool", 60],
  ["host_fs", "Host filesystems, with node_exporter (%)", "host_fs", 60],
  ["host_memory", "Host memory in use, with node_exporter (%)", "host_memory", 120],
  ["host_clock", "Host clocks synchronised, with node_exporter", null, 120],
  ["member_status", "Cluster members offline or evacuated", null, 60],
  ["lxd_security", "LXD releases missing a security fix", null, 0],
  ["instance_oom", "Instances killing processes for lack of memory", null, 0],
  ["instance_rootfs", "Instance root disks (%)", "instance_rootfs", 60],
  ["project_limits", "Project limits.* used (%)", "project_limits", 60],
  ["ovn_uplink", "OVN router addresses used from each uplink's range (%)", "ovn_uplink", 60],
  ["network_addresses", "IPv4 addresses in use on bridge and OVN networks (%)", "network_addresses", 60],
  ["cert_expiry", "Certificates expiring (days left)", "cert_days", 0],
  ["collection", "Metric collection failing (Ceph mgr, node_exporter, LXD)", null, 0],
];
/** Where alerts go (D94): the kinds as the backend lists them, and a few channels. */
const NOTIFY_KINDS = {
  slack: { label: "Slack (or Mattermost)", config: [], secrets: ["url"], required: ["url"], defaults: {} },
  discord: { label: "Discord", config: [], secrets: ["url"], required: ["url"], defaults: {} },
  teams: { label: "Microsoft Teams (Workflows)", config: [], secrets: ["url"], required: ["url"], defaults: {} },
  webhook: { label: "Webhook (JSON)", config: [], secrets: ["url", "signing_secret"], required: ["url"], defaults: {} },
  telegram: { label: "Telegram", config: ["chat_id"], secrets: ["token"], required: ["chat_id", "token"], defaults: {} },
  ntfy: { label: "ntfy", config: ["server", "topic", "username"], secrets: ["token", "password"], required: ["server", "topic"], defaults: { server: "https://ntfy.sh" } },
  smtp: {
    label: "Email (SMTP)",
    config: ["host", "port", "security", "auth", "username", "tenant", "client_id", "token_url", "scope", "sender", "to"],
    secrets: ["password", "client_secret"],
    required: ["host", "port", "security", "sender", "to"], defaults: { port: "587", security: "starttls", auth: "password" },
    choices: { security: ["starttls", "tls", "none"], auth: ["password", "microsoft", "google", "oauth2"] },
    when: {
      tenant: { field: "auth", values: ["microsoft"] },
      client_id: { field: "auth", values: ["microsoft", "google", "oauth2"] },
      client_secret: { field: "auth", values: ["microsoft", "google", "oauth2"] },
      token_url: { field: "auth", values: ["oauth2"] },
      scope: { field: "auth", values: ["oauth2"] },
      password: { field: "auth", values: ["password"] },
    },
  },
};
const CH = (id: number, name: string, kind: string, over: Record<string, unknown> = {}) => ({
  id, name, kind, project: null, enabled: true, min_severity: "warning", config: {}, secrets_set: ["url"], reminder_hours: 4,
  exclude_rules: [], projects: [], created_by: "admin", created_at: 1794400000 - 30 * 86400,
  last: { status: "sent", error: null, created_at: 1794400000 - 1500, sent_at: 1794400000 - 1499 }, ...over,
});
const NOTIFY_CHANNELS = [
  CH(1, "ops-chat", "slack"),
  CH(2, "on-call phone", "ntfy", { min_severity: "critical", config: { server: "https://ntfy.sh", topic: "mcui-5f0c2a9e41d7b38c6a" }, secrets_set: ["token"] }),
  CH(3, "status-hook", "webhook", { reminder_hours: 0, exclude_rules: ["lxd_security"], last: { status: "failed", error: "HTTP 503: Service Unavailable", created_at: 1794400000 - 3600, sent_at: null } }),
  CH(5, "ops-mail", "smtp", { config: { host: "mail.example.org", port: "587", security: "starttls", username: "alerts@example.org", sender: "alerts@example.org", to: "ops@example.org" }, secrets_set: ["password"] }),
  CH(6, "web-gmail", "smtp", { project: "web", config: { host: "smtp.gmail.com", port: "587", security: "starttls", auth: "google", client_id: "1234-demo.apps.googleusercontent.com", username: "web-alerts@example.org", sender: "web-alerts@example.org", to: "web-team@example.org" }, secrets_set: ["client_secret"], created_by: "ops-oncall", last: null }),
  CH(4, "web-team", "telegram", { project: "web", config: { chat_id: "-1001234567890" }, secrets_set: ["token"], created_by: "ops-oncall" }),
];

export const ALERT_SETTINGS: AlertSettingsView = {
  rules: RULE_INFO.map(([name, description, threshold, for_seconds]) => ({ name, description, threshold, for_seconds })),
  thresholds: THRESHOLDS, default_thresholds: THRESHOLDS, thresholds_saved: false,
  disabled: [], disabled_saved: false, enabled: true, interval_seconds: 30, retention_days: 90,
};

/** The Settings page (D87): the built-in hidden list, the browser on with its helpers. */
export const HOST_SETTINGS: HostDeviceSettings = {
  hidden_paths: ["/boot", "/dev", "/etc", "/home", "/proc", "/root", "/run", "/snap", "/sys", "/tmp", "/var/lib", "/var/log", "/var/run", "/var/snap", "/var/tmp"],
  hidden_source: "default",
  default_hidden: [],
  config_hidden: null,
  browse: true,
  browse_source: "app",
  config_browse: false,
  browse_idle_minutes: 10,
  browser: {
    project: "mcui-server",
    exists: true,
    ours: true,
    helpers: [
      { name: "browse-node-1", member: "node-1", status: "Stopped" },
      { name: "browse-node-3", member: "node-3", status: "Running" },
    ],
  },
};

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
    config: {
      ...limits(d),
      "image.os": "Ubuntu",
      "image.release": "resolute",
      ...(GROUPED[d.name] ? { "placement.group": GROUPED[d.name] } : {}),
      ...(d.name === "build-runner" ? { "cluster.evacuate": "migrate" } : {}),
    },
    devices: HOST_DEVICES[d.name] ?? {},
    expanded_config: limits(d),
    expanded_devices: {
      root: { type: "disk", path: "/", pool: "remote" },
      eth0: { type: "nic", network: "default", name: "eth0" },
      ...HOST_DEVICES[d.name],
    },
    created_at: "2026-09-02T10:14:00Z",
    last_used_at: "2026-11-10T07:00:00Z",
    snapshots: [
      { name: "nightly-2026-11-10", created_at: "2026-11-10T02:00:00Z", expires_at: "2026-11-17T02:00:00Z", stateful: false },
      { name: "nightly-2026-11-11", created_at: "2026-11-11T02:00:00Z", expires_at: "2026-11-18T02:00:00Z", stateful: false },
    ],
  };
}

/** Backups of every instance (Phase 7a): a weekly one kept for a month, and one before an upgrade. */
export const BACKUPS: Backup[] = [
  { name: "weekly-2026-11-09", created_at: "2026-11-09T03:00:00Z", expires_at: "2026-12-07T03:00:00Z", optimized_storage: true },
  { name: "before-upgrade", created_at: "2026-11-06T16:20:00Z", expires_at: "0001-01-01T00:00:00Z", instance_only: true },
];

/** Custom volumes, an installer ISO among them (Phase 7d). */
export const VOLUMES: Volume[] = [
  { name: "grafana-data", pool: "remote", type: "custom", content_type: "filesystem", description: "Grafana and Loki data", config: { size: "40GiB" }, location: "none", used_by: ["/1.0/instances/monitoring"] },
  { name: "ubuntu-26.04-live-server-amd64", pool: "remote", type: "custom", content_type: "iso", description: "", config: { size: "3268411392" }, location: "none", used_by: [] },
  { name: "scratch", pool: "local", type: "custom", content_type: "block", description: "Build cache", config: { size: "20GiB" }, location: "node-2", used_by: [] },
];

/** The OVN network's peers (Phase 10a): one working both ways, one waiting for the other side. */
export const PEERS: NetworkPeer[] = [
  { name: "to-web-backend", description: "Front end to the web project's back end", target_project: "web", target_network: "backend", status: "Created", config: {}, used_by: [] },
  { name: "to-lab", description: "", target_project: "lab", target_network: "lab-net", status: "Pending", config: {}, used_by: [] },
];

export function volumeDetail(pool: string, name: string): VolumeDetail | undefined {
  const v = VOLUMES.find((x) => x.pool === pool && x.name === name);
  return v && { ...v, project: "default", state: { usage: { used: 9.6 * GIB, total: 40 * GIB } } };
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
  { name: "remote-fs", driver: "cephfs", status: "Created", description: "Shared filesystems", config: { "cephfs.path": "lxd_cephfs" }, used_by: 2, locations: HOSTS },
  { name: "local", driver: "zfs", status: "Created", description: "Local NVMe storage", config: { source: "/dev/nvme1n1", "zfs.pool_name": "local" }, used_by: 4, locations: HOSTS },
];

export const IMAGES: LocalImage[] = [
  ["ubuntu/26.04", "Ubuntu resolute amd64 (20261101_07:42)", "container", 138e6],
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
  remote("Ubuntu", "26.04", "resolute"),
  remote("Ubuntu", "26.04", "resolute", "cloud"),
  remote("Ubuntu", "24.04", "noble"),
  remote("Ubuntu", "24.04", "noble", "cloud"),
  remote("Ubuntu", "22.04", "jammy"),
  remote("Debian", "13", "trixie"),
  remote("Debian", "12", "bookworm"),
  remote("Alpine", "3.22", "3.22"),
  remote("Fedora", "43", "43"),
  remote("Rockylinux", "10", "10"),
];

// LXD 6.10 keeps image servers itself: the built-ins plus a team mirror
const registry = (name: string, url: string, description: string, builtin = true): ImageRegistry => ({
  name,
  description,
  protocol: "simplestreams",
  builtin,
  public: true,
  config: { url },
});
export const REGISTRIES: ImageRegistry[] = [
  registry("images", "https://images.lxd.canonical.com", "Community images"),
  registry("ubuntu", "https://cloud-images.ubuntu.com/releases", "Ubuntu releases"),
  registry("ubuntu-minimal", "https://cloud-images.ubuntu.com/minimal/releases", "Ubuntu minimal releases"),
  registry("lab-mirror", "https://mirror.example.org/images", "Team mirror", false),
];

// users: cluster roles, and project users with grants (D81)
export const USERS: User[] = [
  { username: "admin", role: "admin", grants: {} },
  { username: "auditor", role: "viewer", grants: {} },
  { username: "dana", role: "project", grants: { web: "admin" } },
  { username: "ops-oncall", role: "project", grants: { web: "operator", lab: "operator" } },
  { username: "student", role: "project", grants: { lab: "viewer" } },
];

// the audit log: changes through the UI and the command line, one refused by LXD
const AUDIT: AuditEntry[] = [
  ["dana", "instance.create", "web/web-3", true, { image: "images:ubuntu/26.04", type: "container" }, "c41e7f0a"],
  ["ops-oncall", "instance.restart", "web/web-2", true, null, "8b2d55e1"],
  ["ops-oncall", "snapshot.create", "lab/sandbox", true, { snapshot: "before-upgrade" }, "f02c9a71"],
  ["admin", "user.grant", "student", true, { project: "lab", role: "viewer" }, null],
  ["dana", "instance.update", "web/cache", false, { error: 'Invalid value "true" for config "security.privileged"' }, null],
  ["admin", "project.create", "lab", true, { restricted: "true" }, "5e9d03bb"],
  [null, "user.create", "ops-oncall", true, { role: "project", via: "cli" }, null],
].map(([username, action, target, ok, detail, op], i) => ({
  id: 40 - i,
  ts: NOW - 600 - i * 1800,
  username: username as string | null,
  action: String(action),
  target: String(target),
  ok: Boolean(ok),
  detail: detail as AuditEntry["detail"],
  operation_id: op as string | null,
}));

// placement groups (LXD 6.6+): keep the default project's services on separate hosts
const GROUPED: Record<string, string> = { "dns-1": "infra-spread", monitoring: "infra-spread", "build-runner": "runners" };
export const PLACEMENT: PlacementGroup[] = [
  { name: "infra-spread", description: "One per host", project: "default", config: { policy: "spread", rigor: "strict" }, used_by: [] },
  { name: "runners", description: "CI runners together", project: "default", config: { policy: "compact", rigor: "permissive" }, used_by: [] },
].map((g) => ({
  ...g,
  config: g.config as PlacementGroup["config"],
  used_by: Object.entries(GROUPED).filter(([, n]) => n === g.name).map(([i]) => `/1.0/instances/${i}?project=default`),
}));

// the default OVN network's load balancer: HTTPS to a health-checked pool of the two
// default-project services, and DNS to a named backend
export const LOAD_BALANCERS: LoadBalancer[] = [
  {
    listen_address: "198.51.100.10",
    description: "Public services",
    config: {},
    backends: [{ name: "dns", target_address: "10.10.0.15", target_port: "53", description: "" }],
    ports: [
      { protocol: "tcp", listen_port: "80,443", target_pool: "front", description: "" },
      { protocol: "udp", listen_port: "53", target_backend: ["dns"], description: "" },
    ],
  },
];
export const LB_POOLS: LbPool[] = [
  { name: "front", description: "", config: { protocol: "tcp", target_port: "443", healthcheck: "true", "healthcheck.interval": "5" }, instances: [{ name: "monitoring" }, { name: "dns-1" }] },
];
export const POOL_STATE: { targets: PoolTarget[] } = {
  targets: [
    { name: "monitoring", address: "10.10.0.16", port: "443", listen_address: "198.51.100.10", listen_port: "443", status: "online" },
    { name: "dns-1", address: "10.10.0.15", port: "443", listen_address: "198.51.100.10", listen_port: "443", status: "online" },
  ],
};

export const NETWORK_DETAIL: NetworkDetail = { ...NETWORKS[1], used_by: [], locations: HOSTS, project: "default" };
export const NETWORK_STATE: NetworkState = {
  addresses: [{ address: "10.10.0.1", family: "inet", netmask: "24", scope: "global" }],
  counters: { bytes_received: 9.1e10, bytes_sent: 5.4e10, packets_received: 7.2e7, packets_sent: 5.1e7 },
  hwaddr: "00:16:3e:00:00:01",
  mtu: 1442,
  state: "up",
  type: "broadcast",
  ovn: { chassis: "node-1" },
};

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
      rootfs_mountpoint: on ? "/" : null,
    };
  }),
};

const hostCpu = [21, 34, 58];
export const HOSTS_VIEW: { node_exporter: boolean; hosts: Host[] } = {
  node_exporter: true,
  hosts: HOSTS.map((name, i): Host => {
    const mine = INSTANCES.filter((d) => d.member === name);
    const on = mine.filter((d) => d.status === "Running");
    const capped = on.filter((d) => !unlimited(d));
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
      allocated_cpus: capped.reduce((s, d) => s + d.cpus, 0),
      allocated_memory: capped.reduce((s, d) => s + d.memory, 0),
      unlimited: on.length - capped.length,
      unlimited_instances: on.filter(unlimited).map((d) => ({ project: d.project, name: d.name, missing: ["cpu", "memory"] })),
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

export function top(metric: string, keep: (project: string) => boolean = () => true): TopResult {
  const on = running.filter((d) => keep(d.project));
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
  { id: 2, name: "lxd_remote", stored: 1.31e12, objects: 341_208, pgs: 128, pct: 0.21 },
  { id: 3, name: "lxd_cephfs_meta", stored: 2.1e8, objects: 1_904, pgs: 16, pct: 0.0001 },
  { id: 4, name: "lxd_cephfs_data", stored: 1.84e11, objects: 51_377, pgs: 32, pct: 0.036 },
];
const OSDS = [0, 1, 2, 3, 4, 5];
// node-3's disks are half as big again: each host holds a full copy, so its extra space
// can't be used (the OSD table's note); osd.2 a little ahead of its host-mate (VAR)
const OSD_SIZE = [3.638e12, 3.638e12, 3.638e12, 3.638e12, 5.457e12, 5.457e12];
const OSD_USED = [0.7e12, 0.79e12, 0.8e12, 0.69e12, 0.72e12, 0.77e12];
const RAW_TOTAL = OSD_SIZE.reduce((a, b) => a + b, 0);

export const CEPH: CephState = {
  enabled: true,
  ok: true,
  mgr: "http://192.0.2.12:9283",
  scraped_at: NOW,
  health: { status: "HEALTH_OK", checks: [] },
  version: "20.2.1 tentacle",
  full_ratios: { nearfull: 0.85, full: 0.95, default: false }, // Tentacle exports them
  // each monitor's data on its host's root filesystem (MON_DISK_LOW at 30% free or less)
  mons: HOSTS.map((name, i) => {
    const size = 480e9;
    const avail = [0.62, 0.41, 0.57][i] * size;
    return {
      name,
      in_quorum: true,
      host: name,
      rank: i,
      address: `192.0.2.${11 + i}`,
      member: name,
      disk: { mountpoint: "/", size, avail, free_percent: Math.floor((avail / size) * 100), state: "ok" as const },
    };
  }),
  mgrs: HOSTS.map((name, i) => ({ name: `${name}.mgr`, active: i === 1 })),
  osds: OSDS.map((id) => ({
    id,
    host: HOSTS[Math.floor(id / 2)],
    device_class: "nvme",
    up: true,
    in: true,
    pgs: [61, 66, 68, 59, 63, 64][id],
    size: OSD_SIZE[id],
    used: OSD_USED[id],
    apply_latency_ms: 1 + (id % 3),
    commit_latency_ms: 1 + (id % 3),
  })),
  pgs: { total: 177, states: { active: 177, clean: 177 } },
  capacity: { total: RAW_TOTAL, used_raw: OSD_USED.reduce((a, b) => a + b, 0) },
  pools: CEPH_POOLS.map((p) => ({
    id: p.id,
    name: p.name,
    type: "replicated",
    description: "replica:3",
    stored: p.stored,
    max_avail: 4.9e12,
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
  ceph_cluster_total_bytes: { sets: [{}], base: () => RAW_TOTAL, wobble: 0 },
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

// who the screenshots are taken as: the admin, or (D81) a project user, for whom the demo
// keeps to their projects as the real server does
let signedIn: User = USERS[0];
export function signIn(username: string): void {
  signedIn = USERS.find((u) => u.username === username) ?? USERS[0];
}
const mine = (project: string) => signedIn.role !== "project" || project in (signedIn.grants ?? {});

/** The API answer for a request, or ``undefined`` for an unknown path. */
export function answer(method: string, url: URL): unknown {
  const p = url.pathname;
  const s = url.searchParams;
  if (method !== "GET") return {};
  const fixed: Record<string, unknown> = {
    "/api/auth/me": { user: signedIn, csrf: "demo" },
    "/api/health": { status: "ok", version: "0.1.5", lxd_configured: true },
    "/api/server": SERVER,
    "/api/metrics/live":
      signedIn.role === "project" ? { ...LIVE, members: [], instances: LIVE.instances.filter((i) => mine(i.project)) } : LIVE,
    "/api/hosts": HOSTS_VIEW,
    "/api/cluster/members": MEMBERS,
    "/api/projects": PROJECTS.filter((x) => mine(x.name)),
    "/api/instances": LIST.filter((i) => mine(i.project)),
    "/api/storage-pools": POOLS,
    "/api/profiles": PROFILES,
    "/api/images": IMAGES,
    "/api/ceph": CEPH,
    "/api/ovn": OVN,
    "/api/operations": [],
    "/api/warnings": [],
    "/api/volumes": VOLUMES,
    "/api/network-acls": [],
    "/api/network-allocations": [],
    "/api/image-registries": REGISTRIES,
    "/api/users": USERS,
    "/api/settings/host-devices": HOST_SETTINGS,
    "/api/settings/alerts": ALERT_SETTINGS,
    "/api/notify/kinds": NOTIFY_KINDS,
    "/api/notify/oauth": { redirect_uri: "https://mcui.example.org/oauth/callback" },
    "/api/notify/channels": NOTIFY_CHANNELS,
    "/api/projects/web/notify/channels": NOTIFY_CHANNELS.filter((c) => c.project === "web"),
    "/api/audit": signedIn.role === "project" ? AUDIT.filter((e) => mine(String(e.target).split("/")[0]) && e.target?.includes("/")) : AUDIT,
    "/api/placement-groups": PLACEMENT,
    "/api/networks/default/default": NETWORK_DETAIL,
    "/api/networks/default/default/state": NETWORK_STATE,
    "/api/networks/default/default/members": [],
    "/api/networks/default/default/leases": [],
    "/api/networks/default/default/forwards": [],
    "/api/networks/default/default/load-balancers": LOAD_BALANCERS,
    "/api/networks/default/default/peers": PEERS,
    "/api/networks/default/default/load-balancer-pools": LB_POOLS,
    "/api/networks/default/default/load-balancer-pools/front/state": POOL_STATE,
  };
  if (p in fixed) return fixed[p];
  if (p === "/api/networks") return s.get("target") ? HOST_INTERFACES : NETWORKS;
  if (p === "/api/alerts") {
    // a project user sees only their projects' (D91)
    const rows = s.get("state") === "history" ? ALERTS_HISTORY : ALERTS_ACTIVE;
    return signedIn.role === "project" ? rows.filter((a) => a.project !== null && mine(a.project)) : rows;
  }
  if (p === "/api/metrics/query") return query(s);
  if (p === "/api/metrics/top") {
    const t = top(s.get("metric") ?? "cpu", signedIn.role === "project" ? mine : undefined);
    return t;
  }
  if (p === "/api/metrics/network-traffic") return traffic(Number(s.get("range") ?? 3600));
  if (p === "/api/ceph/rbd") return { metric: s.get("metric"), range: Number(s.get("range")), rows: RBD };
  if (p.startsWith("/api/images/remote/")) return REMOTE_IMAGES;
  let m = /^\/api\/cluster\/members\/[^/]+\/resources$/.exec(p);
  if (m) return RESOURCES;
  m = /^\/api\/instances\/([^/]+)\/([^/]+)\/host-devices$/.exec(p);
  if (m) {
    const i = instanceFull(decodeURIComponent(m[1]), decodeURIComponent(m[2]));
    return i && HOST_INVENTORY(i.location);
  }
  m = /^\/api\/instances\/([^/]+)\/([^/]+)\/host-files$/.exec(p);
  if (m) {
    const i = instanceFull(decodeURIComponent(m[1]), decodeURIComponent(m[2]));
    return i && hostFolder(i.location, s.get("path") ?? "/");
  }
  m = /^\/api\/instances\/([^/]+)\/([^/]+)\/backups$/.exec(p);
  if (m) return instanceFull(decodeURIComponent(m[1]), decodeURIComponent(m[2])) ? BACKUPS : undefined;
  m = /^\/api\/volumes\/default\/([^/]+)\/([^/]+)(\/snapshots|\/backups)?$/.exec(p);
  if (m) {
    const v = volumeDetail(decodeURIComponent(m[1]), decodeURIComponent(m[2]));
    return v && (m[3] === "/snapshots" ? [] : m[3] === "/backups" ? BACKUPS.slice(0, 1) : v);
  }
  m = /^\/api\/projects\/([^/]+)$/.exec(p);
  if (m) return PROJECTS.find((x) => x.name === decodeURIComponent(m![1]) && mine(x.name));
  m = /^\/api\/instances\/([^/]+)\/([^/]+)$/.exec(p);
  if (m) return instanceFull(decodeURIComponent(m[1]), decodeURIComponent(m[2]));
  return undefined;
}
