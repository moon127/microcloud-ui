export const poolPath = (name: string) => `/storage/pools/${encodeURIComponent(name)}`;
export const volumePath = (project: string, pool: string, name: string, member?: string) =>
  `/storage/volumes/${encodeURIComponent(project)}/${encodeURIComponent(pool)}/${encodeURIComponent(name)}${
    member ? `?member=${encodeURIComponent(member)}` : ""
  }`;
