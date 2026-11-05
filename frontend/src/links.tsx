import { Link } from "react-router";
import { instancePath } from "./paths";

export function InstanceLink({ project, name }: { project: string; name: string }) {
  return <Link to={instancePath(project, name)}>{project === "default" ? name : `${project}/${name}`}</Link>;
}

export function MemberLink({ name }: { name: string }) {
  return <Link to={`/members/${encodeURIComponent(name)}`}>{name}</Link>;
}
