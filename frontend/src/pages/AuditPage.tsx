import { useState } from "react";
import { fetchAudit, type AuditEntry } from "../api";
import { useActions } from "../actions";
import { useApi } from "../useApi";

const PAGE = 100;

/** "key=value, …" for an entry's details. */
function details(d: AuditEntry["detail"]): string {
  if (!d) return "";
  return Object.entries(d)
    .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(", ");
}

/** Who changed what (AU-3). The server decides what each user sees: everything for cluster
 * admins and viewers, their projects' changes for project users (D81). */
export default function AuditPage() {
  const { version } = useActions();
  const first = useApi(`audit:${version}`, () => fetchAudit(PAGE), 30_000);
  const [older, setOlder] = useState<AuditEntry[]>([]);
  const [more, setMore] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [text, setText] = useState("");

  const newest = first.data ?? [];
  const seen = new Set(newest.map((e) => e.id));
  const all = [...newest, ...older.filter((e) => !seen.has(e.id))];
  const q = text.trim().toLowerCase();
  const rows = q
    ? all.filter((e) => `${e.username ?? ""} ${e.action} ${e.target ?? ""} ${details(e.detail)}`.toLowerCase().includes(q))
    : all;

  const loadOlder = async () => {
    const last = all[all.length - 1];
    if (!last) return;
    setLoading(true);
    setError("");
    try {
      const page = await fetchAudit(PAGE, last.id);
      setOlder((o) => [...o, ...page]);
      setMore(page.length === PAGE);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <h1>Audit log</h1>
      <p className="muted">Every change made through microcloud-ui, by whom. LXD itself records them all as microcloud-ui's own certificate.</p>
      {first.error && <p role="alert">{first.error}</p>}
      {!first.data && !first.error && <p role="status">Loading…</p>}
      {first.data && (
        <>
          <label className="field">
            Filter
            <input value={text} placeholder="user, action, target or detail" onChange={(e) => setText(e.target.value)} />
          </label>
          {rows.length === 0 ? (
            <p>{all.length ? "Nothing matches." : "Nothing yet."}</p>
          ) : (
            <div className="scroll">
              <table>
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>User</th>
                    <th>Action</th>
                    <th>Target</th>
                    <th>Result</th>
                    <th>Details</th>
                    <th>LXD operation</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((e) => (
                    <tr key={e.id}>
                      <td>{new Date(e.ts * 1000).toLocaleString()}</td>
                      <td>{e.username ?? "(command line)"}</td>
                      <td>{e.action}</td>
                      <td className="wrap">{e.target ?? ""}</td>
                      <td>{e.ok ? "ok" : <strong role="alert">failed</strong>}</td>
                      <td className="wrap">{details(e.detail)}</td>
                      <td className="muted">{e.operation_id ?? ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {error && <p role="alert">{error}</p>}
          {more && all.length >= PAGE && (
            <p>
              <button type="button" disabled={loading} onClick={() => void loadOlder()}>
                {loading ? "Loading…" : "Load older"}
              </button>
            </p>
          )}
        </>
      )}
    </>
  );
}
