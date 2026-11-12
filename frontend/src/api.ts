export interface Health {
  status: string;
  version: string;
  lxd_configured?: boolean;
}

export type Role = "admin" | "viewer";

export interface User {
  username: string;
  role: Role;
}

export interface Me {
  user: User;
  csrf: string;
}

export interface ServerInfo {
  version: string;
  clustered: boolean;
  server_name: string;
  auth: string;
  auth_method: string | null;
  endpoint: string;
  features: Record<string, boolean>;
}

export interface ScrapeStatus {
  member: string;
  url: string;
  port: number | null;
  ok: boolean;
  error: string | null;
  fell_back: boolean;
  samples: number;
}

export interface MemberLive {
  name: string;
  url: string;
  status: string;
  roles: string[];
  failure_domain: string;
  message: string;
  architecture: string;
  scrape: ScrapeStatus;
  daemon: Partial<Record<"uptime_seconds" | "goroutines" | "warnings" | "api_requests_ongoing" | "operations", number>>;
  instances: number;
}

export interface InstanceLive {
  member: string;
  project: string;
  name: string;
  type: string;
  cpu_percent: number | null;
  cpus: number | null;
  memory_total: number | null;
  memory_used: number | null;
  memory_rss: number | null;
  memory_cached: number | null;
  memory_swap: number | null;
  oom_kills: number | null;
  disk_read_bps: number | null;
  disk_write_bps: number | null;
  disk_read_iops: number | null;
  disk_write_iops: number | null;
  net_rx_bps: number | null;
  net_tx_bps: number | null;
  procs: number | null;
  rootfs_size: number | null;
  rootfs_used: number | null;
  /** "/" normally; another mountpoint when the VM agent didn't report "/" (D62). */
  rootfs_mountpoint: string | null;
}

export interface LiveMetrics {
  scraped_at: number | null;
  interval_seconds: number;
  members: MemberLive[];
  instances: InstanceLive[];
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

let csrfToken: string | null = null;

export function setCsrf(token: string | null): void {
  csrfToken = token;
}

const SAFE = new Set(["GET", "HEAD", "OPTIONS"]);

export async function api<T>(path: string, init: RequestInit = {}, fetchFn: typeof fetch = fetch): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (init.body !== undefined) headers.set("Content-Type", "application/json");
  if (!SAFE.has(method) && csrfToken) headers.set("X-CSRF-Token", csrfToken);
  const res = await fetchFn(path, { ...init, method, headers, credentials: "same-origin" });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // not JSON: keep the status text
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export async function fetchHealth(fetchFn: typeof fetch = fetch): Promise<Health> {
  return api<Health>("/api/health", {}, fetchFn);
}

export async function fetchMe(): Promise<Me> {
  const me = await api<Me>("/api/auth/me");
  setCsrf(me.csrf);
  return me;
}

export async function login(username: string, password: string): Promise<Me> {
  const me = await api<Me>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
  setCsrf(me.csrf);
  return me;
}

export async function logout(): Promise<void> {
  await api<void>("/api/auth/logout", { method: "POST" });
  setCsrf(null);
}

export const fetchServer = () => api<ServerInfo>("/api/server");
export const fetchLiveMetrics = () => api<LiveMetrics>("/api/metrics/live");

// --- Phase 2: history ------------------------------------------------------

export type Point = [number, number];
export type Labels = Record<string, string>;

export interface Series {
  labels: Labels;
  points: Point[];
}

export interface QueryResult {
  name: string;
  kind: "counter" | "gauge";
  resolution: number;
  fn: "value" | "rate" | "max";
  start: number;
  end: number;
  series: Series[];
}

export interface MetricQuery {
  name: string;
  match?: Labels;
  fn?: "value" | "rate" | "max";
  by?: string[];
  range: number;
}

export function queryMetric(q: MetricQuery): Promise<QueryResult> {
  const p = new URLSearchParams({ name: q.name, range: String(q.range) });
  if (q.fn) p.set("fn", q.fn);
  for (const [k, v] of Object.entries(q.match ?? {})) p.append("match", `${k}=${v}`);
  for (const b of q.by ?? []) p.append("by", b);
  return api<QueryResult>(`/api/metrics/query?${p}`);
}

export type TopMetric = "cpu" | "memory" | "net" | "disk";

export interface TopResult {
  metric: TopMetric;
  unit: "percent" | "bytes" | "bytes_per_second";
  rows: { project: string; name: string; value: number }[];
}

export function fetchTop(metric: TopMetric, range: number, limit = 10): Promise<TopResult> {
  return api<TopResult>(`/api/metrics/top?metric=${metric}&range=${range}&limit=${limit}`);
}

export interface Instance {
  project: string;
  name: string;
  type: string;
  status: string;
  location: string;
  description: string;
  profiles?: string[];
  nics: Record<string, string>;
}

export const fetchInstances = () => api<Instance[]>("/api/instances");

export interface Snapshot {
  name: string;
  created_at: string;
  expires_at?: string;
  stateful: boolean;
}

/** The full LXD instance, as GET /1.0/instances/{name}?recursion=1 returns it. */
export interface InstanceFull {
  project: string;
  name: string;
  type: string;
  status: string;
  location: string;
  description: string;
  architecture?: string;
  ephemeral?: boolean;
  stateful?: boolean;
  profiles: string[];
  config: Record<string, string>;
  devices: Record<string, Record<string, string>>;
  expanded_config?: Record<string, string>;
  expanded_devices?: Record<string, Record<string, string>>;
  created_at?: string;
  last_used_at?: string;
  snapshots?: Snapshot[] | null;
}

const inst = (project: string, name: string) =>
  `/api/instances/${encodeURIComponent(project)}/${encodeURIComponent(name)}`;

export function fetchInstance(project: string, name: string): Promise<InstanceFull> {
  return api<InstanceFull>(inst(project, name));
}

export type StateAction = "start" | "stop" | "restart" | "freeze" | "unfreeze";

export const changeState = (project: string, name: string, action: StateAction, force = false) =>
  send<Started>("PUT", `${inst(project, name)}/state`, { action, force });

export const deleteInstance = (project: string, name: string, force = false) =>
  send<Started>("DELETE", `${inst(project, name)}${force ? "?force=true" : ""}`);

export interface InstanceUpdate {
  description: string;
  config: Record<string, string>;
  devices: Record<string, Record<string, string>>;
  profiles: string[];
  ephemeral: boolean;
  stateful: boolean;
  architecture?: string;
}

export const updateInstance = (project: string, name: string, body: InstanceUpdate) =>
  send<Started>("PUT", inst(project, name), body);
export const patchInstanceConfig = (project: string, name: string, config: Record<string, string>) =>
  send<Started>("PATCH", `${inst(project, name)}/config`, { config });
export const renameInstance = (project: string, name: string, to: string) =>
  send<Started>("POST", `${inst(project, name)}/rename`, { name: to });
export const moveInstance = (project: string, name: string, opts: { target?: string; pool?: string; live?: boolean }) =>
  send<Started>("POST", `${inst(project, name)}/move`, opts);
export const copyInstance = (
  project: string,
  name: string,
  opts: { name: string; snapshot?: string; target?: string; start?: boolean; instance_only?: boolean },
) => send<Started>("POST", `${inst(project, name)}/copy`, opts);
export const createSnapshot = (
  project: string,
  name: string,
  opts: { name: string; stateful?: boolean; expires_at?: string },
) => send<Started>("POST", `${inst(project, name)}/snapshots`, opts);
export const renameSnapshot = (project: string, name: string, snap: string, to: string) =>
  send<Started>("POST", `${inst(project, name)}/snapshots/${encodeURIComponent(snap)}/rename`, { name: to });
export const deleteSnapshot = (project: string, name: string, snap: string) =>
  send<Started>("DELETE", `${inst(project, name)}/snapshots/${encodeURIComponent(snap)}`);
export const restoreSnapshot = (project: string, name: string, snap: string) =>
  send<Started>("POST", `${inst(project, name)}/snapshots/${encodeURIComponent(snap)}/restore`, {});
export const publishInstance = (project: string, name: string, opts: { alias?: string; snapshot?: string }) =>
  send<Started>("POST", `${inst(project, name)}/publish`, opts);

export const fetchLogs = (project: string, name: string) => api<string[]>(`${inst(project, name)}/logs`);

export async function fetchLog(project: string, name: string, file: string): Promise<string> {
  const res = await fetch(`${inst(project, name)}/logs/${encodeURIComponent(file)}`, { credentials: "same-origin" });
  if (!res.ok) throw new ApiError(res.status, `HTTP ${res.status}`);
  return res.text();
}

export interface StoragePool {
  name: string;
  driver: string;
  status: string;
  description: string;
  config?: Record<string, string>;
  /** how many objects use it */
  used_by?: number;
  locations?: string[];
}

export const fetchStoragePools = () => api<StoragePool[]>("/api/storage-pools");

export interface Member {
  name: string;
  url: string;
  status: string;
  roles: string[];
  failure_domain: string;
  message: string;
  architecture: string;
}

export const fetchMembers = () => api<Member[]>("/api/cluster/members");

// Subset of LXD's /1.0/resources that the member page shows.
export interface Resources {
  cpu: {
    total: number;
    architecture?: string;
    sockets?: { name?: string; vendor?: string; cores?: { threads?: unknown[] }[] }[];
  };
  memory: { total: number; used?: number };
  gpu?: { total: number; cards?: { product?: string; vendor?: string; driver?: string }[] };
  network?: {
    cards?: {
      product?: string;
      vendor?: string;
      driver?: string;
      ports?: { id: string; link_detected?: boolean; link_speed?: number }[];
    }[];
  };
  storage?: { disks?: { id: string; model?: string; type?: string; size: number }[] };
  system?: { vendor?: string; product?: string };
}

export function fetchMemberResources(name: string): Promise<Resources> {
  return api<Resources>(`/api/cluster/members/${encodeURIComponent(name)}/resources`);
}

export interface NetworkTraffic {
  start: number;
  end: number;
  resolution: number;
  rx: Series[];
  tx: Series[];
}

export function fetchNetworkTraffic(range: number): Promise<NetworkTraffic> {
  return api<NetworkTraffic>(`/api/metrics/network-traffic?range=${range}`);
}

// --- Phase 3: operations, warnings, projects, member state ---------------------

export interface Operation {
  id: string;
  class?: string;
  description: string;
  created_at: string;
  updated_at?: string;
  status: string;
  status_code?: number;
  resources?: Record<string, string[]> | null;
  metadata?: Record<string, unknown> | null;
  may_cancel?: boolean;
  err?: string;
  location?: string;
  project?: string;
  /** LXD 6.8+: a bulk operation's own operations (recursion=2) */
  children?: Operation[];
  child_count?: number;
}

/** Response of every mutating call: an LXD operation (async) or plain metadata. */
export interface Started {
  operation: Operation | null;
  metadata: unknown;
}

export function send<T>(method: string, path: string, body?: unknown): Promise<T> {
  return api<T>(path, { method, body: body === undefined ? undefined : JSON.stringify(body) });
}

export const fetchOperations = () => api<Operation[]>("/api/operations");
export const waitOperation = (id: string, wait = 30) =>
  api<Operation>(`/api/operations/${encodeURIComponent(id)}?wait=${wait}`);
/** LXD's progress note for a running operation ("rootfs: 45% (12.3MB/s)", "Unpack: 80%"), if any. */
export function operationProgress(op: Operation): string | null {
  const notes = Object.entries(op.metadata ?? {})
    .filter(([k]) => k.endsWith("progress"))
    .map(([, v]) => {
      if (typeof v === "string") return v;
      if (v && typeof v === "object") {
        const { stage, percent, speed } = v as Record<string, unknown>;
        return [stage, percent != null && `${percent}%`, speed && `(${speed})`].filter(Boolean).join(" ");
      }
      return "";
    })
    .filter(Boolean);
  return notes.length ? notes.join(" · ") : null;
}
export const cancelOperation = (id: string) => send<void>("DELETE", `/api/operations/${encodeURIComponent(id)}`);

export interface LxdWarning {
  uuid: string;
  project?: string;
  type: string;
  status: string;
  severity: string;
  count: number;
  first_seen_at?: string;
  last_seen_at: string;
  last_message: string;
  entity_url?: string;
  location?: string;
}

export const fetchWarnings = () => api<LxdWarning[]>("/api/warnings");
export const setWarningStatus = (uuid: string, status: "new" | "acknowledged") =>
  send<Started>("PUT", `/api/warnings/${encodeURIComponent(uuid)}`, { status });
export const deleteWarning = (uuid: string) => send<void>("DELETE", `/api/warnings/${encodeURIComponent(uuid)}`);

export type EvacuationMode = "auto" | "migrate" | "live-migrate" | "stop";
export const evacuateMember = (name: string, mode?: EvacuationMode, force = false) =>
  send<Started>("POST", `/api/cluster/members/${encodeURIComponent(name)}/evacuate`, {
    ...(mode ? { mode } : {}),
    ...(force ? { force } : {}),
  });
export const restoreMember = (name: string) =>
  send<Started>("POST", `/api/cluster/members/${encodeURIComponent(name)}/restore`, {});

export interface Project {
  name: string;
  description: string;
  config: Record<string, string>;
  used_by: string[];
}

export const fetchProjects = () => api<Project[]>("/api/projects");

// --- Phase 3c: create instance -------------------------------------------------

export interface LocalImage {
  fingerprint: string;
  aliases: string[];
  description: string;
  type: string;
  architecture: string;
  size: number;
  created_at: string;
  uploaded_at: string;
  last_used_at: string;
  cached: boolean;
  public: boolean;
  auto_update: boolean;
  properties: Record<string, string>;
  project: string;
}

export interface RemoteImage {
  alias: string;
  aliases: string[];
  os: string;
  release: string;
  release_title: string;
  variant: string;
  arch: string;
  container: boolean;
  vm: boolean;
  version: string;
}

export interface Profile {
  name: string;
  description: string;
  config: Record<string, string>;
  devices: Record<string, Record<string, string>>;
  used_by: string[];
  project: string;
}

export interface Network {
  name: string;
  type: string;
  managed: boolean;
  description: string;
  status: string;
  config: Record<string, string>;
  /** how many objects use it */
  used_by?: number;
  locations?: string[];
}

const q = (project: string) => `?project=${encodeURIComponent(project)}`;
export const fetchLocalImages = (project: string) => api<LocalImage[]>(`/api/images${q(project)}`);
export const fetchRemoteImages = (remote: "images" | "ubuntu") => api<RemoteImage[]>(`/api/images/remote/${remote}`);
export const fetchProfiles = (project: string) => api<Profile[]>(`/api/profiles${q(project)}`);

/** LXD 6.6+ (instance_placement_groups): spread or pack instances across members. */
export interface PlacementGroup {
  name: string;
  description: string;
  project: string;
  config: { policy?: "spread" | "compact"; rigor?: "strict" | "permissive" } & Record<string, string>;
  used_by: string[];
}
export interface PlacementGroupBody {
  description: string;
  policy: "spread" | "compact";
  rigor: "strict" | "permissive";
}
export const fetchPlacementGroups = (project: string) => api<PlacementGroup[]>(`/api/placement-groups${q(project)}`);
const pgPath = (project: string, name: string) =>
  `/api/placement-groups/${encodeURIComponent(project)}/${encodeURIComponent(name)}`;
export const createPlacementGroup = (project: string, name: string, body: PlacementGroupBody) =>
  send<Started>("POST", "/api/placement-groups", { name, project, ...body });
export const updatePlacementGroup = (project: string, name: string, body: PlacementGroupBody) =>
  send<Started>("PUT", pgPath(project, name), body);
export const deletePlacementGroup = (project: string, name: string) => send<Started>("DELETE", pgPath(project, name));
export const fetchNetworks = (project: string, target?: string) =>
  api<Network[]>(`/api/networks${q(project)}${target ? `&target=${encodeURIComponent(target)}` : ""}`);

export interface NewInstance {
  name: string;
  type: "container" | "virtual-machine";
  project: string;
  target?: string;
  image: { remote: "local" | "images" | "ubuntu"; alias?: string; fingerprint?: string };
  description: string;
  profiles: string[];
  config: Record<string, string>;
  devices: Record<string, Record<string, string>>;
  start: boolean;
}

export const createInstance = (body: NewInstance) => send<Started>("POST", "/api/instances", body);

// --- Phase 3d: consoles ---------------------------------------------------------

export interface StreamStart {
  id: string;
  kind: "exec" | "console" | "vga";
  operation: string;
}

export const startExec = (project: string, name: string, size: { cols: number; rows: number }) =>
  send<StreamStart>("POST", `${inst(project, name)}/exec`, size);
export const startConsole = (
  project: string,
  name: string,
  opts: { type: "console" | "vga"; cols?: number; rows?: number },
) => send<StreamStart>("POST", `${inst(project, name)}/console`, opts);

export async function fetchConsoleLog(project: string, name: string): Promise<string> {
  const res = await fetch(`${inst(project, name)}/console-log`, { credentials: "same-origin" });
  const text = await res.text();
  if (!res.ok) throw new ApiError(res.status, text || `HTTP ${res.status}`);
  return text;
}

export const clearConsoleLog = (project: string, name: string) =>
  send<Started>("DELETE", `${inst(project, name)}/console-log`);

/** The browser websocket for a started terminal/console stream. */
export function streamUrl(id: string): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/api/streams/${encodeURIComponent(id)}`;
}

// --- Phase 3e: file browser ---------------------------------------------------------

export interface FileMeta {
  type: "file" | "directory" | "symlink" | "unknown" | string;
  mode?: string;
  uid?: string;
  gid?: string;
  modified?: string;
  size?: number | null;
}

export interface FileEntry extends FileMeta {
  name: string;
}

export interface FileView extends FileMeta {
  path: string;
  entries?: FileEntry[];
  truncated?: boolean;
  target?: string;
  resolved?: string;
}

const files = (project: string, name: string) => `${inst(project, name)}/files`;
const withPath = (path: string) => `?path=${encodeURIComponent(path)}`;

export const browseFiles = (project: string, name: string, path: string) =>
  api<FileView>(`${files(project, name)}${withPath(path)}`);

/** A same-origin link the browser can download (the session cookie authorises it). */
export const downloadUrl = (project: string, name: string, path: string) =>
  `${files(project, name)}/download${withPath(path)}`;

export async function uploadFile(project: string, name: string, path: string, file: Blob, mode = "0644"): Promise<Started> {
  const res = await fetch(`${files(project, name)}/upload${withPath(path)}&mode=${mode}`, {
    method: "POST",
    body: file,
    credentials: "same-origin",
    headers: { "Content-Type": "application/octet-stream", ...(csrfToken ? { "X-CSRF-Token": csrfToken } : {}) },
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
    throw new ApiError(res.status, typeof body.detail === "string" ? body.detail : `HTTP ${res.status}`);
  }
  return (await res.json()) as Started;
}

export const makeDirectory = (project: string, name: string, path: string) =>
  send<Started>("POST", `${files(project, name)}/mkdir${withPath(path)}`);
export const deleteFile = (project: string, name: string, path: string) =>
  send<Started>("DELETE", `${files(project, name)}${withPath(path)}`);

// --- Phase 3f: profiles, images, projects -------------------------------------------

const pq = (project: string, name: string) => `${encodeURIComponent(project)}/${encodeURIComponent(name)}`;

export interface ProfileFull extends Profile {
  name: string;
}

export const fetchProfile = (project: string, name: string) => api<ProfileFull>(`/api/profiles/${pq(project, name)}`);
export const createProfile = (
  project: string,
  name: string,
  body: { description: string; config: Record<string, string>; devices: Record<string, Record<string, string>> },
) => send<Started>("POST", "/api/profiles", { project, name, ...body });
export const updateProfile = (
  project: string,
  name: string,
  body: { description: string; config: Record<string, string>; devices: Record<string, Record<string, string>> },
) => send<Started>("PUT", `/api/profiles/${pq(project, name)}`, body);
export const renameProfile = (project: string, name: string, to: string) =>
  send<Started>("POST", `/api/profiles/${pq(project, name)}/rename`, { name: to });
export const deleteProfile = (project: string, name: string) => send<Started>("DELETE", `/api/profiles/${pq(project, name)}`);

export const updateImage = (
  project: string,
  fingerprint: string,
  body: { properties: Record<string, string>; public: boolean; auto_update: boolean },
) => send<Started>("PUT", `/api/images/${pq(project, fingerprint)}`, body);
export const deleteImage = (project: string, fingerprint: string) =>
  send<Started>("DELETE", `/api/images/${pq(project, fingerprint)}`);
export const addImageAlias = (project: string, fingerprint: string, alias: string) =>
  send<Started>("POST", `/api/images/${pq(project, fingerprint)}/aliases`, { name: alias });
export const deleteImageAlias = (project: string, alias: string) =>
  send<Started>("DELETE", `/api/images/${encodeURIComponent(project)}/aliases/${alias.split("/").map(encodeURIComponent).join("/")}`);
export const importImage = (body: {
  project: string;
  remote: "images" | "ubuntu";
  alias: string;
  local_alias?: string;
  auto_update: boolean;
  vm: boolean;
}) => send<Started>("POST", "/api/images/import", body);

export async function uploadImage(project: string, file: File, alias?: string): Promise<Started> {
  const q = new URLSearchParams({ project, filename: file.name, ...(alias ? { alias } : {}) });
  const res = await fetch(`/api/images/upload?${q}`, {
    method: "POST",
    body: file,
    credentials: "same-origin",
    headers: { "Content-Type": "application/octet-stream", ...(csrfToken ? { "X-CSRF-Token": csrfToken } : {}) },
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
    throw new ApiError(res.status, typeof body.detail === "string" ? body.detail : `HTTP ${res.status}`);
  }
  return (await res.json()) as Started;
}

export const fetchProject = (name: string) => api<Project>(`/api/projects/${encodeURIComponent(name)}`);
export const createProject = (name: string, body: { description: string; config: Record<string, string> }) =>
  send<Started>("POST", "/api/projects", { name, ...body });
export const updateProject = (name: string, body: { description: string; config: Record<string, string> }) =>
  send<Started>("PUT", `/api/projects/${encodeURIComponent(name)}`, body);
export const renameProject = (name: string, to: string) =>
  send<Started>("POST", `/api/projects/${encodeURIComponent(name)}/rename`, { name: to });
export const deleteProject = (name: string) => send<Started>("DELETE", `/api/projects/${encodeURIComponent(name)}`);

// --- Phase 4a: networks ----------------------------------------------------------

export type NetworkType = "bridge" | "ovn" | "physical" | "macvlan";

export interface NetworkDetail {
  name: string;
  type: string;
  managed: boolean;
  description: string;
  status: string;
  config: Record<string, string>;
  used_by: string[] | null;
  locations: string[] | null;
  project?: string;
}

export interface NetworkState {
  addresses: { address: string; family: string; netmask: string; scope: string }[] | null;
  counters?: { bytes_received: number; bytes_sent: number; packets_received: number; packets_sent: number } | null;
  hwaddr: string;
  mtu: number;
  state: string;
  type: string;
  ovn?: { chassis?: string } | null;
  vlan?: { lower_device: string; vid: number } | null;
  bond?: { mode?: string; lower_devices?: string[] } | null;
  bridge?: { upper_devices?: string[] } | null;
}

export interface NetworkMember {
  member: string;
  config: Record<string, string>;
  error?: string;
}

export interface NetworkLease {
  hostname: string;
  hwaddr: string;
  address: string;
  type: string;
  location: string;
  project: string;
}

export interface NetworkAllocation {
  addresses: string;
  used_by: string;
  type: string;
  nat: boolean;
  hwaddr: string;
  network: string;
}

export interface NewNetwork {
  name: string;
  type: NetworkType;
  project: string;
  description: string;
  config: Record<string, string>;
  /** member name -> that member's own keys (parent, bridge.external_interfaces) */
  members: Record<string, Record<string, string>>;
}

const net = (project: string, name: string) => `/api/networks/${encodeURIComponent(project)}/${encodeURIComponent(name)}`;
const tq = (target?: string) => (target ? `?target=${encodeURIComponent(target)}` : "");

export const fetchNetwork = (project: string, name: string, target?: string) =>
  api<NetworkDetail>(`${net(project, name)}${tq(target)}`);
export const fetchNetworkState = (project: string, name: string, target?: string) =>
  api<NetworkState>(`${net(project, name)}/state${tq(target)}`);
export const fetchNetworkMembers = (project: string, name: string) => api<NetworkMember[]>(`${net(project, name)}/members`);
export const fetchNetworkLeases = (project: string, name: string) => api<NetworkLease[]>(`${net(project, name)}/leases`);
export const fetchNetworkAllocations = (project: string) => api<NetworkAllocation[]>(`/api/network-allocations${q(project)}`);
export const createNetwork = (body: NewNetwork) => send<Started>("POST", "/api/networks", body);
export const updateNetwork = (
  project: string,
  name: string,
  body: { description?: string; config: Record<string, string> },
  target?: string,
) => send<Started>("PUT", `${net(project, name)}${tq(target)}`, body);
export const deleteNetwork = (project: string, name: string) => send<Started>("DELETE", net(project, name));

// --- Phase 4b: network ACLs and forwards -------------------------------------------

export interface AclRule {
  action: "allow" | "allow-stateless" | "drop" | "reject";
  state: "enabled" | "disabled" | "logged";
  description: string;
  source: string;
  destination: string;
  protocol: "" | "tcp" | "udp" | "icmp4" | "icmp6";
  source_port: string;
  destination_port: string;
  icmp_type: string;
  icmp_code: string;
}

export interface NetworkAcl {
  name: string;
  description: string;
  ingress: AclRule[] | null;
  egress: AclRule[] | null;
  config: Record<string, string>;
  used_by: string[] | null;
  project?: string;
}

export interface AclBody {
  description: string;
  ingress: AclRule[];
  egress: AclRule[];
  config: Record<string, string>;
}

const acl = (project: string, name: string) => `/api/network-acls/${encodeURIComponent(project)}/${encodeURIComponent(name)}`;
export const fetchAcls = (project: string) => api<NetworkAcl[]>(`/api/network-acls${q(project)}`);
export const fetchAcl = (project: string, name: string) => api<NetworkAcl>(acl(project, name));
export const createAcl = (project: string, name: string, body: AclBody) =>
  send<Started>("POST", "/api/network-acls", { name, project, ...body });
export const updateAcl = (project: string, name: string, body: AclBody) => send<Started>("PUT", acl(project, name), body);
export const renameAcl = (project: string, name: string, to: string) => send<Started>("POST", `${acl(project, name)}/rename`, { name: to });
export const deleteAcl = (project: string, name: string) => send<Started>("DELETE", acl(project, name));

export interface ForwardPort {
  protocol: "tcp" | "udp";
  listen_port: string;
  target_address: string;
  target_port: string;
  description: string;
}

export interface NetworkForward {
  listen_address: string;
  description: string;
  config: Record<string, string>;
  ports: ForwardPort[] | null;
  location?: string;
}

export interface ForwardBody {
  description: string;
  config: Record<string, string>;
  ports: ForwardPort[];
}

const fwd = (project: string, network: string, listen?: string) =>
  `${net(project, network)}/forwards${listen ? `/${encodeURIComponent(listen)}` : ""}`;
export const fetchForwards = (project: string, network: string) => api<NetworkForward[]>(fwd(project, network));
export const createForward = (project: string, network: string, body: ForwardBody & { listen_address: string }, target?: string) =>
  send<Started>("POST", `${fwd(project, network)}${tq(target)}`, body);
export const updateForward = (project: string, network: string, listen: string, body: ForwardBody, target?: string) =>
  send<Started>("PUT", `${fwd(project, network, listen)}${tq(target)}`, body);
export const deleteForward = (project: string, network: string, listen: string, target?: string) =>
  send<Started>("DELETE", `${fwd(project, network, listen)}${tq(target)}`);

// --- Phase 4c: storage pools ------------------------------------------------------

export type PoolDriver = "dir" | "zfs" | "btrfs" | "lvm" | "ceph" | "cephfs";

export interface PoolDetail {
  name: string;
  driver: string;
  description: string;
  status: string;
  config: Record<string, string>;
  used_by: string[] | null;
  locations: string[] | null;
}

export interface PoolResources {
  /** CephFS pools report no ``used`` */
  space: { used?: number; total: number };
  inodes?: { used: number; total: number };
}

export interface PoolMember {
  member: string;
  config: Record<string, string>;
  status?: string;
  resources?: PoolResources;
  error?: string;
}

export interface NewPool {
  name: string;
  driver: PoolDriver;
  description: string;
  config: Record<string, string>;
  members: Record<string, Record<string, string>>;
}

const pool = (name: string) => `/api/storage-pools/${encodeURIComponent(name)}`;
export const fetchPool = (name: string) => api<PoolDetail>(pool(name));
export const fetchPoolMembers = (name: string) => api<PoolMember[]>(`${pool(name)}/members`);
export const createPool = (body: NewPool) => send<Started>("POST", "/api/storage-pools", body);
export const updatePool = (name: string, body: { description?: string; config: Record<string, string> }, target?: string) =>
  send<Started>("PUT", `${pool(name)}${tq(target)}`, body);
export const deletePool = (name: string) => send<Started>("DELETE", pool(name));

// --- Phase 4d: custom volumes -------------------------------------------------------

export interface Volume {
  name: string;
  type: string;
  content_type: "filesystem" | "block" | "iso" | string;
  description: string;
  config: Record<string, string>;
  location: string;
  used_by: string[] | null;
  project?: string;
  pool: string;
  created_at?: string;
}

export interface VolumeDetail extends Volume {
  state: { usage?: { used: number; total?: number } | null } | null;
}

export interface VolumeSnapshot {
  name: string;
  description: string;
  created_at: string;
  expires_at?: string | null;
  config: Record<string, string>;
}

export interface NewVolume {
  name: string;
  pool: string;
  project: string;
  content_type: "filesystem" | "block";
  description: string;
  config: Record<string, string>;
  target?: string;
}

/** Volumes on local pools in a cluster live on one member; LXD wants it back as ``target``. */
export const volumeTarget = (v: { location?: string }) => (v.location && v.location !== "none" ? v.location : undefined);

const vol = (project: string, pool: string, name: string) =>
  `/api/volumes/${encodeURIComponent(project)}/${encodeURIComponent(pool)}/${encodeURIComponent(name)}`;
export const fetchVolumes = (project: string) => api<Volume[]>(`/api/volumes${q(project)}`);
export const fetchVolume = (project: string, pool: string, name: string, target?: string) =>
  api<VolumeDetail>(`${vol(project, pool, name)}${tq(target)}`);
export const fetchVolumeSnapshots = (project: string, pool: string, name: string, target?: string) =>
  api<VolumeSnapshot[]>(`${vol(project, pool, name)}/snapshots${tq(target)}`);
export const createVolume = (body: NewVolume) => send<Started>("POST", "/api/volumes", body);
export const updateVolume = (
  project: string,
  pool: string,
  name: string,
  body: { description: string; config: Record<string, string> },
  target?: string,
) => send<Started>("PUT", `${vol(project, pool, name)}${tq(target)}`, body);
export const renameVolume = (project: string, pool: string, name: string, to: string, target?: string) =>
  send<Started>("POST", `${vol(project, pool, name)}/rename${tq(target)}`, { name: to });
export const deleteVolume = (project: string, pool: string, name: string, target?: string) =>
  send<Started>("DELETE", `${vol(project, pool, name)}${tq(target)}`);
export const createVolumeSnapshot = (
  project: string,
  pool: string,
  name: string,
  body: { name: string; expires_at?: string },
  target?: string,
) => send<Started>("POST", `${vol(project, pool, name)}/snapshots${tq(target)}`, body);
const vsnap = (project: string, pool: string, name: string, snap: string) =>
  `${vol(project, pool, name)}/snapshots/${encodeURIComponent(snap)}`;
export const renameVolumeSnapshot = (project: string, pool: string, name: string, snap: string, to: string, target?: string) =>
  send<Started>("POST", `${vsnap(project, pool, name, snap)}/rename${tq(target)}`, { name: to });
export const restoreVolumeSnapshot = (project: string, pool: string, name: string, snap: string, target?: string) =>
  send<Started>("POST", `${vsnap(project, pool, name, snap)}/restore${tq(target)}`);
export const deleteVolumeSnapshot = (project: string, pool: string, name: string, snap: string, target?: string) =>
  send<Started>("DELETE", `${vsnap(project, pool, name, snap)}${tq(target)}`);
export const attachVolume = (project: string, pool: string, name: string, body: { instance: string; device: string; path?: string }) =>
  send<Started>("POST", `${vol(project, pool, name)}/attach`, body);
export const detachVolume = (project: string, pool: string, name: string, body: { instance: string; device: string }) =>
  send<Started>("POST", `${vol(project, pool, name)}/detach`, body);

// --- Phase 4e: Ceph dashboard ----------------------------------------------------------

export interface CephOsd {
  id: number;
  host?: string;
  device_class?: string;
  up?: boolean;
  in?: boolean;
  weight?: number;
  pgs?: number;
  size?: number;
  used?: number;
  apply_latency_ms?: number;
  commit_latency_ms?: number;
}

export interface CephPool {
  id: number;
  name?: string;
  type?: string;
  description?: string;
  stored?: number;
  stored_raw?: number;
  max_avail?: number;
  /** 0..1 */
  percent_used?: number;
  objects?: number;
  pgs?: number;
}

export interface CephState {
  enabled: boolean;
  ok?: boolean;
  error?: string;
  mgr?: string;
  scraped_at?: number;
  health?: { status: string; checks: { name: string; severity: string }[] };
  version?: string | null;
  mons?: { name: string; in_quorum: boolean }[];
  mgrs?: { name: string; active: boolean }[];
  osds?: CephOsd[];
  /** false: the mgr leaves out OSD perf counters (Ceph Tentacle's default) */
  perf_counters?: boolean;
  pgs?: { total: number; states: Record<string, number> };
  capacity?: { total: number | null; used_raw: number | null };
  pools?: CephPool[];
}

export type RbdMetric = "read_bytes" | "write_bytes" | "read_ops" | "write_ops" | "read_latency" | "write_latency";

export interface RbdOwner {
  kind: "instance" | "volume" | "image" | "other";
  type?: string;
  project?: string;
  name?: string;
  block?: boolean;
  deleted?: boolean;
}

export interface RbdRow {
  pool: string;
  image: string;
  value: number;
  owner: RbdOwner;
}

export const fetchCeph = () => api<CephState>("/api/ceph");
export const fetchRbdTop = (metric: RbdMetric, range: number, limit = 10) =>
  api<{ metric: RbdMetric; range: number; rows: RbdRow[] }>(`/api/ceph/rbd?metric=${metric}&range=${range}&limit=${limit}`);

// --- Phase 4f: OVN overview -------------------------------------------------------------

export interface OvnNetwork {
  name: string;
  project: string;
  description: string;
  status: string;
  uplink: string;
  ipv4: string;
  ipv6: string;
  ipv4_nat: boolean;
  ipv6_nat: boolean;
  router_ipv4: string;
  router_ipv6: string;
  acls: string[];
  used_by: number;
  chassis: string | null;
  state_error: string | null;
  /** null: LXD couldn't list them */
  forwards: unknown[] | null;
  load_balancers: unknown[] | null;
  peers: unknown[] | null;
  allocations: Record<string, number>;
}

export interface OvnUplink {
  name: string;
  status: string;
  ipv4_gateway: string;
  ipv6_gateway: string;
  ipv4_ranges: string;
  ipv6_ranges: string;
  ipv4_routes: string;
  dns: string;
  bgp_peers: string[];
  networks: string[];
  ipv4_used: number;
  ipv4_size: number;
}

export const fetchOvn = () => api<{ networks: OvnNetwork[]; uplinks: OvnUplink[] }>("/api/ovn");

// --- Phase 4g: hosts (LXD + node_exporter) ----------------------------------------------

export interface NodeNow {
  url: string;
  ok: boolean;
  error: string | null;
  absent: boolean;
  memory_total?: number | null;
  memory_used?: number | null;
  memory_cache?: number | null;
  zfs_arc?: number | null;
  swap_total?: number | null;
  swap_used?: number | null;
  rootfs_size?: number;
  rootfs_used?: number;
  timex_sync?: number | null;
  timex_offset?: number | null;
  cpu_percent?: number;
  cpu_iowait_percent?: number;
  cpu_steal_percent?: number;
  disk_busy_percent?: number;
  disk_busiest?: string;
  disk_read_bps?: number;
  disk_write_bps?: number;
  pressure_cpu?: number;
  pressure_memory?: number;
  pressure_io?: number;
}

export interface Host {
  name: string;
  status: string;
  roles: string[];
  cpus: number | null;
  load1: number | null;
  load5: number | null;
  uptime: number | null;
  memory_total: number | null;
  memory_free: number | null;
  memory_buffered: number | null;
  swap_total: number | null;
  swap_free: number | null;
  instances: number;
  instances_running: number;
  instances_cpu: number;
  instances_memory: number;
  allocated_cpus: number;
  allocated_memory: number;
  unlimited: number;
  /** Running instances with no CPU and/or memory limit (containers; VMs get LXD's defaults). */
  unlimited_instances: { project: string; name: string; missing: ("cpu" | "memory")[] }[];
  net_rx_bps: number | null;
  net_tx_bps: number | null;
  node: NodeNow | null;
}

export const fetchHosts = () => api<{ node_exporter: boolean; hosts: Host[] }>("/api/hosts");
