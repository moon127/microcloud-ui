import { Link } from "react-router";
import { instancePath } from "./paths";

export function InstanceLink({ project, name }: { project: string; name: string }) {
  return <Link to={instancePath(project, name)}>{project === "default" ? name : `${project}/${name}`}</Link>;
}

export function MemberLink({ name }: { name: string }) {
  return <Link to={`/members/${encodeURIComponent(name)}`}>{name}</Link>;
}

const KINDS: Record<string, (project: string, name: string) => string> = {
  instances: instancePath,
  profiles: (p, n) => `/profiles/${encodeURIComponent(p)}/${encodeURIComponent(n)}`,
  networks: (p, n) => `/networks/${encodeURIComponent(p)}/${encodeURIComponent(n)}`,
  "network-acls": (p, n) => `/network-acls/${encodeURIComponent(p)}/${encodeURIComponent(n)}`,
};

/** "/1.0/instances/web?project=lab" -> link to that object (instances, profiles, networks, ACLs); else the URL. */
export function UsedBy({ url }: { url: string }) {
  const u = new URL(url, "http://x");
  const v = u.pathname.match(/^\/1\.0\/storage-pools\/([^/]+)\/volumes\/custom\/([^/]+)$/);
  if (v) {
    const project = u.searchParams.get("project") ?? "default";
    const [pool, name] = [decodeURIComponent(v[1]), decodeURIComponent(v[2])];
    const target = u.searchParams.get("target");
    return (
      <Link
        to={`/storage/volumes/${encodeURIComponent(project)}/${encodeURIComponent(pool)}/${encodeURIComponent(name)}${
          target ? `?member=${encodeURIComponent(target)}` : ""
        }`}
      >
        volume {pool}/{name}
      </Link>
    );
  }
  const m = u.pathname.match(/^\/1\.0\/([a-z-]+)\/([^/]+)$/);
  const to = m && KINDS[m[1]];
  if (!m || !to) return <code>{url}</code>;
  const project = u.searchParams.get("project") ?? "default";
  const name = decodeURIComponent(m[2]);
  return (
    <Link to={to(project, name)}>
      {m[1] === "instances" ? "" : `${m[1].slice(0, -1)} `}
      {project === "default" ? name : `${project}/${name}`}
    </Link>
  );
}
