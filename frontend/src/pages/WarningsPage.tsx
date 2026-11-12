import { useState } from "react";
import { deleteWarning, fetchWarnings, setWarningStatus, type LxdWarning } from "../api";
import { useActions } from "../actions";
import Confirm from "../Confirm";
import { useSession } from "../session";
import { useApi } from "../useApi";

export default function WarningsPage() {
  const { user } = useSession();
  const { run, version } = useActions();
  const { data, error } = useApi(`warnings:${version}`, fetchWarnings, 60_000);
  const [deleting, setDeleting] = useState<LxdWarning | null>(null);
  const admin = user.role === "admin";

  return (
    <>
      <h1>Warnings</h1>
      {error && <p role="alert">{error}</p>}
      {!data && !error && <p role="status">Loading…</p>}
      {data && data.length === 0 && <p>No warnings.</p>}
      {data && data.length > 0 && (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>Severity</th>
                <th>Type</th>
                <th>Message</th>
                <th>Member</th>
                <th>Project</th>
                <th className="num">Count</th>
                <th>Last seen</th>
                <th>Status</th>
                {admin && <th />}
              </tr>
            </thead>
            <tbody>
              {data.map((w) => (
                <tr key={w.uuid}>
                  <td>{w.severity}</td>
                  <td>{w.type}</td>
                  <td className="wrap">{w.last_message}</td>
                  <td>{w.location ?? ""}</td>
                  <td>{w.project ?? ""}</td>
                  <td className="num">{w.count}</td>
                  <td>{new Date(w.last_seen_at).toLocaleString()}</td>
                  <td>{w.status}</td>
                  {admin && (
                    <td>
                      {w.status === "acknowledged" ? (
                        <button type="button" onClick={() => void run(`Reopen warning ${w.type}`, () => setWarningStatus(w.uuid, "new"))}>
                          Reopen
                        </button>
                      ) : (
                        <button
                          type="button"
                          onClick={() => void run(`Acknowledge ${w.type}`, () => setWarningStatus(w.uuid, "acknowledged"))}
                        >
                          Acknowledge
                        </button>
                      )}{" "}
                      <button type="button" className="danger" onClick={() => setDeleting(w)}>
                        Delete
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {deleting && (
        <Confirm
          title="Delete warning?"
          confirmLabel="Delete"
          danger
          onCancel={() => setDeleting(null)}
          onConfirm={() => {
            const w = deleting;
            setDeleting(null);
            void run(`Delete warning ${w.type}`, () => deleteWarning(w.uuid));
          }}
        >
          <p>
            {w(deleting)} LXD recreates it if the condition is still present.
          </p>
        </Confirm>
      )}
    </>
  );
}

const w = (x: LxdWarning) => `"${x.type}" on ${x.location || "the cluster"}.`;
