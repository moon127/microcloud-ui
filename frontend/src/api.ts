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
