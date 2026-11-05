import { useState } from "react";
import { cancelOperation, fetchOperations, type Operation } from "../api";
import { useActions } from "../actions";
import { useEvents } from "../events";
import { useSession } from "../session";
import { useApi } from "../useApi";

function resources(op: Operation): string {
  return Object.values(op.resources ?? {})
    .flat()
    .map((r) => r.split("/").pop())
    .join(", ");
}

export default function OperationsPage() {
  const { user } = useSession();
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
              {data.map((op) => (
                <tr key={op.id}>
                  <td>{new Date(op.created_at).toLocaleString()}</td>
                  <td>{op.description}</td>
                  <td>
                    {op.status}
                    {op.err ? `: ${op.err}` : ""}
                  </td>
                  <td>{resources(op)}</td>
                  <td>{op.project ?? ""}</td>
                  <td>{op.location ?? ""}</td>
                  <td>
                    {user.role === "admin" && op.may_cancel && op.status === "Running" && (
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
