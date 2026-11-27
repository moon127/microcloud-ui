import { Link } from "react-router";
import type { AlertCenter } from "./alertCenter";

/** The top bar's alert count. */
export function AlertBell({ center }: { center: AlertCenter }) {
  const { open, critical } = center;
  const warnings = open.length - critical.length;
  return (
    <Link
      to="/alerts"
      className={`bell ${critical.length ? "critical" : warnings ? "warning" : ""}`}
      aria-label={`Alerts: ${critical.length} critical, ${warnings} warning`}
      title="Alerts"
    >
      🔔{open.length > 0 && <span className="count">{open.length}</span>}
    </Link>
  );
}

/** Under the top bar: a banner while anything critical is unacknowledged, and toasts for
 * new alerts (warnings go after 20 s, critical ones stay until dismissed). */
export function AlertNotices({ center }: { center: AlertCenter }) {
  const { critical, toasts, dismiss } = center;
  return (
    <>
      {critical.length > 0 && (
        <div className="alert-banner" role="alert">
          {critical.length === 1 ? critical[0].title : `${critical.length} critical alerts: ${critical[0].title}, …`}{" "}
          <Link to="/alerts">View</Link>
        </div>
      )}
      {/* always there, so screen readers announce the first toast too */}
      <ul className="alert-toasts" aria-label="New alerts" aria-live="polite">
        {toasts.map((a) => (
            <li key={a.id} className={`toast alert-${a.severity}`}>
              <strong>{a.severity === "critical" ? "Critical" : "Warning"}:</strong>{" "}
              {a.link ? <Link to={a.link}>{a.title}</Link> : a.title}
              <div className="muted">{a.detail}</div>
              <button type="button" className="link" aria-label={`Dismiss ${a.title}`} onClick={() => dismiss(a.id)}>
                Dismiss
              </button>
            </li>
          ))}
      </ul>
    </>
  );
}
