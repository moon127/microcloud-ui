import type { SharedKind } from "./shared";

const LABEL: Record<SharedKind, string> = {
  profiles: "profiles",
  images: "images",
  "storage.volumes": "storage volumes",
  networks: "networks",
};

/** Said where write actions would be, in a project sharing default's objects (D89). */
export default function SharedNote({ project, kind }: { project: string; kind: SharedKind }) {
  return (
    <p className="muted">
      {project} shares the default project&apos;s {LABEL[kind]}, so only a cluster admin can change them.
    </p>
  );
}
