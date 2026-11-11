export const networkPath = (project: string, name: string, member?: string) =>
  `/networks/${encodeURIComponent(project)}/${encodeURIComponent(name)}${member ? `?member=${encodeURIComponent(member)}` : ""}`;
