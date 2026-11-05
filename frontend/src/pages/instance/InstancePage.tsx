import { useParams, useSearchParams } from "react-router";
import { fetchInstance } from "../../api";
import { useActions } from "../../actions";
import { MemberLink } from "../../links";
import { useApi } from "../../useApi";
import Actions from "./Actions";
import ConfigTab from "./ConfigTab";
import Graphs from "./Graphs";
import LogsTab from "./LogsTab";
import SnapshotsTab from "./SnapshotsTab";

const TABS = [
  { id: "overview", label: "Overview" },
  { id: "config", label: "Configuration" },
  { id: "snapshots", label: "Snapshots" },
  { id: "logs", label: "Logs" },
] as const;

export default function InstancePage() {
  const { project = "default", name = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const { version } = useActions();
  const tab = TABS.find((t) => t.id === params.get("tab"))?.id ?? "overview";
  const info = useApi(`instance:${project}/${name}:${version}`, () => fetchInstance(project, name));
  const inst = info.data;

  return (
    <>
      <h1>
        {project === "default" ? name : `${project}/${name}`}
        {inst && (
          <span className="muted">
            {" "}
            {inst.type === "virtual-machine" ? "VM" : "container"} · {inst.status}
            {inst.location && inst.location !== "none" && (
              <>
                {" "}
                · on <MemberLink name={inst.location} />
              </>
            )}
          </span>
        )}
      </h1>
      {info.error && <p role="alert">{info.error}</p>}
      {inst && <Actions inst={inst} />}
      <nav className="tabs" aria-label="Instance sections">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            aria-pressed={t.id === tab}
            onClick={() => {
              const next = new URLSearchParams(params);
              next.set("tab", t.id);
              setParams(next);
            }}
          >
            {t.label}
          </button>
        ))}
      </nav>
      {tab === "overview" && <Graphs project={project} name={name} />}
      {tab === "config" && inst && <ConfigTab inst={inst} />}
      {tab === "snapshots" && inst && <SnapshotsTab inst={inst} />}
      {tab === "logs" && <LogsTab project={project} name={name} />}
    </>
  );
}
