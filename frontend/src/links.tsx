import { Link } from "react-router";

const instancePath = (project: string, name: string) =>
  `/instances/${encodeURIComponent(project)}/${encodeURIComponent(name)}`;

export function InstanceLink({ project, name }: { project: string; name: string }) {
  return <Link to={instancePath(project, name)}>{project === "default" ? name : `${project}/${name}`}</Link>;
}

export function MemberLink({ name }: { name: string }) {
  return <Link to={`/members/${encodeURIComponent(name)}`}>{name}</Link>;
}
