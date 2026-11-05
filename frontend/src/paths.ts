export const instancePath = (project: string, name: string) =>
  `/instances/${encodeURIComponent(project)}/${encodeURIComponent(name)}`;
