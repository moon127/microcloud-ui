import { Link, useSearchParams } from "react-router";
import { ackAlert, fetchAlerts, type AlertRow } from "../api";
import { useAccess } from "../access";
import { useActions } from "../actions";
import { formatDuration } from "../format";
import { useApi } from "../useApi";
import WarningsPage from "./WarningsPage";

const TABS = [
  { id: "active", label: "Active" },
  { id: "history", label: "History" },
  { id: "lxd", label: "LXD warnings" },
] as const;

/** When, readably: local date and time. */
const when = (t: number) => new Date(t * 1000).toLocaleString();

/** Alerts (Phase 8a, D91): what's wrong now, what was, and LXD's own warnings. */
export default function AlertsPage() {
  const access = useAccess();
  const [params, setParams] = useSearchParams();
  const tabs = TABS.filter((t) => t.id !== "lxd" || access.cluster);
  const tab = tabs.find((t) => t.id === params.get("tab"))?.id ?? "active";
  return (
    <>
      <h1>Alerts</h1>
      <nav className="tabs" aria-label="Alert sections">
        {tabs.map((t) => (
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
      {tab === "active" && <AlertTable state="active" />}
      {tab === "history" && <AlertTable state="history" />}
      {tab === "lxd" && <WarningsPage embedded />}
    </>
  );
}

function AlertTable({ state }: { state: "active" | "history" }) {
  const access = useAccess();
  const { run, version } = useActions();
  const { data, error } = useApi(`alerts:${state}:${version}`, () => fetchAlerts(state), 15_000);
  const active = state === "active";
  const mayAck = (a: AlertRow) => access.clusterAdmin || (a.project !== null && access.can(a.project, "manage"));
  return (
    <>
      <p className="muted">
        {active
          ? "Checked every 30 seconds unless the config says otherwise. Acknowledging hides an alert from everyone's bell and banner until it clears and comes back, or turns critical."
          : "Alerts that have cleared, newest first."}
      </p>
      {error && <p role="alert">{error}</p>}
      {!data && !error && <p role="status">Loading…</p>}
      {data && data.length === 0 && <p>{active ? "Nothing wrong right now." : "No cleared alerts yet."}</p>}
      {data && data.length > 0 && (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>Severity</th>
                <th>Alert</th>
                <th>Since</th>
                <th>{active ? "Acknowledged" : "Cleared"}</th>
              </tr>
            </thead>
            <tbody>
              {data.map((a) => (
                <tr key={a.id} className={`alert-${a.severity}`}>
                  <td>
                    <span className={`badge ${a.severity}`}>{a.severity}</span>
                  </td>
                  <td>
                    {a.link ? <Link to={a.link}>{a.title}</Link> : a.title}
                    <div className="muted">{a.detail}</div>
                  </td>
                  <td>{when(a.fired_at)}</td>
                  <td>
                    {active ? (
                      a.ack_by ? (
                        `by ${a.ack_by}`
                      ) : mayAck(a) ? (
                        <button type="button" onClick={() => void run(`Acknowledge ${a.title}`, () => ackAlert(a.id).then(() => undefined))}>
                          Acknowledge
                        </button>
                      ) : (
                        "–"
                      )
                    ) : (
                      `${when(a.resolved_at ?? a.last_seen)} (after ${formatDuration((a.resolved_at ?? a.last_seen) - a.fired_at)})`
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
