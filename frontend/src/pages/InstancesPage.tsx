import { useMemo, useState } from "react";
import { fetchInstances } from "../api";
import { InstanceLink, MemberLink } from "../links";
import { useApi } from "../useApi";

export default function InstancesPage() {
  const { data, error } = useApi("instances", fetchInstances, 60_000);
  const [filter, setFilter] = useState("");
  const rows = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return (data ?? []).filter(
      (i) => !q || `${i.name} ${i.project} ${i.location} ${i.status} ${i.type}`.toLowerCase().includes(q),
    );
  }, [data, filter]);

  return (
    <>
      <h1>Instances</h1>
      {error && <p role="alert">{error}</p>}
      <input
        type="search"
        placeholder="Filter by name, project, member, status or type"
        aria-label="Filter instances"
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
      />
      {!data && !error && <p role="status">Loading…</p>}
      {data && (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Project</th>
                <th>Type</th>
                <th>Status</th>
                <th>Member</th>
                <th>Networks</th>
                <th>Description</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((i) => (
                <tr key={`${i.project}/${i.name}`}>
                  <td>
                    <InstanceLink project={i.project} name={i.name} />
                  </td>
                  <td>{i.project}</td>
                  <td>{i.type === "virtual-machine" ? "VM" : "container"}</td>
                  <td>{i.status}</td>
                  <td>{i.location ? <MemberLink name={i.location} /> : "–"}</td>
                  <td>{Array.from(new Set(Object.values(i.nics))).join(", ") || "–"}</td>
                  <td>{i.description}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {rows.length === 0 && <p>No instances.</p>}
        </div>
      )}
    </>
  );
}
