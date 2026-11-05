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
  nics: Record<string, string>;
}

export const fetchInstances = () => api<Instance[]>("/api/instances");

export function fetchInstance(project: string, name: string): Promise<Instance> {
  return api<Instance>(`/api/instances/${encodeURIComponent(project)}/${encodeURIComponent(name)}`);
}

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
