export const networkPath = (project: string, name: string, member?: string) =>
  `/networks/${encodeURIComponent(project)}/${encodeURIComponent(name)}${member ? `?member=${encodeURIComponent(member)}` : ""}`;
export const aclPath = (project: string, name: string) => `/network-acls/${encodeURIComponent(project)}/${encodeURIComponent(name)}`;
