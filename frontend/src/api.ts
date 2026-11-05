export interface Health {
  status: string;
  version: string;
}

export async function fetchHealth(fetchFn: typeof fetch = fetch): Promise<Health> {
  const res = await fetchFn("/api/health");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as Health;
}
