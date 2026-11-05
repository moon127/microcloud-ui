import { useState } from "react";
import { fetchLog, fetchLogs } from "../../api";
import { useApi } from "../../useApi";

export default function LogsTab({ project, name }: { project: string; name: string }) {
  const files = useApi(`logs:${project}/${name}`, () => fetchLogs(project, name));
  const [chosen, setChosen] = useState<string | null>(null);
  const file = chosen ?? files.data?.[0] ?? null;
  const content = useApi(`log:${project}/${name}/${file}`, () => (file ? fetchLog(project, name, file) : Promise.resolve("")));

  return (
    <section aria-label="Logs">
      {files.error && <p role="alert">{files.error}</p>}
      {files.data && files.data.length === 0 && <p>No log files.</p>}
      {files.data && files.data.length > 0 && (
        <>
          <label className="field">
            Log file{" "}
            <select value={file ?? ""} onChange={(e) => setChosen(e.target.value)}>
              {files.data.map((f) => (
                <option key={f}>{f}</option>
              ))}
            </select>
          </label>
          {content.error && <p role="alert">{content.error}</p>}
          <pre className="log" aria-label="Log content">
            {content.data ?? "Loading…"}
          </pre>
        </>
      )}
    </section>
  );
}
