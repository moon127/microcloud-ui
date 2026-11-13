import { useState } from "react";
import { cancelOperation, fetchOperations, operationProgress, type Operation } from "../api";
import { useActions } from "../actions";
import { useEvents } from "../events";
import { useApi } from "../useApi";
import { useAccess } from "../access";

/** The objects an operation works on: ``resources`` (LXD 5.x), else its ``entity_url`` (LXD 6.7+). */
function objects(op: Operation): string {
  const name = (url: string) => decodeURIComponent(url.split("?")[0].split("/").pop() ?? "");
  const listed = Object.values(op.resources ?? {}).flat();
  if (listed.length) return listed.map(name).join(", ");
  const entity = op.metadata?.entity_url;
  return typeof entity === "string" ? name(entity) : "";
}

export default function OperationsPage() {
  const access = useAccess();
  const { run, version } = useActions();
  const [tick, setTick] = useState(0);
  useEvents((e) => e.type === "operation" && setTick((t) => t + 1));
  const { data, error } = useApi(`operations:${tick}:${version}`, fetchOperations, 30_000);

  return (
    <>
      <h1>Operations</h1>
      {error && <p role="alert">{error}</p>}
      {!data && !error && <p role="status">Loading…</p>}
      {data && data.length === 0 && <p>No recent operations.</p>}
      {data && data.length > 0 && (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>Started</th>
                <th>Description</th>
                <th>Status</th>
                <th>Objects</th>
                <th>Project</th>
                <th>Member</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {data.flatMap((op) => [op, ...(op.children ?? []).map((c) => ({ ...c, parent: op.id }))]).map((op) => (
                <tr key={op.id} className={"parent" in op ? "child" : undefined}>
                  <td>{"parent" in op ? "" : new Date(op.created_at).toLocaleString()}</td>
                  <td>
                    {"parent" in op && <span aria-hidden="true">↳ </span>}
                    {op.description}
                    {!!op.child_count && <span className="muted"> ({op.child_count} operations)</span>}
                    {operationProgress(op) && <div className="muted">{operationProgress(op)}</div>}
                  </td>
                  <td>
                    {op.status}
                    {op.err ? `: ${op.err}` : ""}
                  </td>
                  <td>{objects(op)}</td>
                  <td>{op.project ?? ""}</td>
                  <td>{op.location ?? ""}</td>
                  <td>
                    {access.clusterAdmin && op.may_cancel && op.status === "Running" && (
                      <button type="button" onClick={() => void run(`Cancel ${op.description}`, () => cancelOperation(op.id))}>
                        Cancel
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
