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
export const evacuateMember = (name: string, mode?: EvacuationMode) =>
  send<Started>("POST", `/api/cluster/members/${encodeURIComponent(name)}/evacuate`, mode ? { mode } : {});
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
}

const q = (project: string) => `?project=${encodeURIComponent(project)}`;
export const fetchLocalImages = (project: string) => api<LocalImage[]>(`/api/images${q(project)}`);
export const fetchRemoteImages = (remote: "images" | "ubuntu") => api<RemoteImage[]>(`/api/images/remote/${remote}`);
export const fetchProfiles = (project: string) => api<Profile[]>(`/api/profiles${q(project)}`);
export const fetchNetworks = (project: string) => api<Network[]>(`/api/networks${q(project)}`);

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
