import { useState } from "react";
import { useNavigate, useParams } from "react-router";
import { deletePool, fetchPool, fetchPoolMembers, updatePool, type PoolDriver, type PoolMember } from "../../api";
import { useActions } from "../../actions";
import MetricPanel from "../../chart/MetricPanel";
import { poolSeries } from "../../chart/panel";
import Confirm from "../../Confirm";
import { formatBytes, formatNumber, formatPercent } from "../../format";
import { MemberLink, UsedBy } from "../../links";
import MemberConfig from "../../MemberConfig";
import { useRange } from "../../range";
import RangePicker from "../../RangePicker";
import { useApi } from "../../useApi";
import YamlEditor from "../../YamlEditor";
import { asConfig } from "../instance/state";
import { LOCAL } from "./request";
import { useAccess } from "../../access";

interface Draft {
  description: string;
  config: Record<string, string>;
}

export default function PoolPage() {
  const { name = "" } = useParams();
  const access = useAccess();
  const { run, version } = useActions();
  const navigate = useNavigate();
  const range = useRange();
  const pool = useApi(`pool:${name}:${version}`, () => fetchPool(name));
  const members = useApi(`pool-members:${name}:${version}`, () => fetchPoolMembers(name), 60_000);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const admin = access.clusterAdmin;
  const p = pool.data;

  // take the loaded values into the form as they arrive, during render rather than in an
  // effect, so the form never shows a render's worth of stale values
  const [shown, setShown] = useState<unknown>(null);
  if (p !== shown) {
    setShown(p);
    if (p) setDraft({ description: p.description, config: p.config });
  }

  return (
    <>
      <h1>
        Storage pool {name} <span className="muted">{p ? `${p.driver} · ${p.status}` : ""}</span>
      </h1>
      {pool.error && <p role="alert">{pool.error}</p>}
      {p && draft && (
        <>
          {admin && (
            <div className="actions" role="toolbar" aria-label="Pool actions">
              <button
                type="button"
                className="primary"
                disabled={problem !== null}
                onClick={() => void run(`Save pool ${name}`, () => updatePool(name, draft))}
              >
                Save
              </button>
              <button type="button" className="danger" onClick={() => setDeleting(true)}>
                Delete
              </button>
            </div>
          )}
          <h2>Capacity</h2>
          {members.error && <p role="alert">{members.error}</p>}
          {members.data && <Capacity rows={members.data} />}
          <RangePicker />
          <div className="grid">
            <MetricPanel
              title={`${name}: space used`}
              range={range}
              format={formatBytes}
              queries={[
                { name: "mcui_pool_space_used", match: { pool: name } },
                { name: "mcui_pool_space_total", match: { pool: name } },
              ]}
              compute={poolSeries}
            />
          </div>
          <h2>Settings</h2>
          <fieldset disabled={!admin}>
            <label className="field">
              Description
              <input value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} />
            </label>
          </fieldset>
          <YamlEditor
            label="Config (YAML), cluster-wide"
            rows={8}
            value={draft.config}
            readOnly={!admin}
            onChange={(v) => {
              const c = asConfig(v);
              setProblem(typeof c === "string" ? c : null);
              if (typeof c !== "string") setDraft((d) => d && { ...d, config: c });
            }}
          />
          {problem && <p role="alert">{problem}</p>}
          {members.data && members.data.length > 1 && LOCAL.has(p.driver as PoolDriver) && (
            <>
              <h2>Per-member settings</h2>
              <div className="grid">
                {members.data.map((m) => (
                  <MemberConfig
                    key={m.member}
                    member={m.member}
                    config={m.config}
                    error={m.error}
                    admin={admin}
                    onSave={(config) => void run(`Save ${name} on ${m.member}`, () => updatePool(name, { config }, m.member))}
                  />
                ))}
              </div>
            </>
          )}
          <h2>Used by</h2>
          {(p.used_by ?? []).length === 0 ? (
            <p>Nothing.</p>
          ) : (
            <ul>
              {(p.used_by ?? []).map((u) => (
                <li key={u}>
                  <UsedBy url={u} />
                </li>
              ))}
            </ul>
          )}
        </>
      )}
      {deleting && (
        <Confirm
          title={`Delete storage pool ${name}?`}
          confirmLabel="Delete"
          danger
          onCancel={() => setDeleting(false)}
          onConfirm={() => {
            setDeleting(false);
            void run(`Delete pool ${name}`, () => deletePool(name)).then((ok) => ok && navigate("/storage"));
          }}
        >
          <p>Pools with volumes, images or instances on them can't be deleted.</p>
        </Confirm>
      )}
    </>
  );
}

function Capacity({ rows }: { rows: PoolMember[] }) {
  return (
    <div className="scroll">
      <table>
        <thead>
          <tr>
            <th>Member</th>
            <th>Status</th>
            <th className="num">Used</th>
            <th className="num">Size</th>
            <th>Used %</th>
            <th className="num">Inodes used</th>
            <th>Source</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((m) => {
            const s = m.resources?.space;
            const pct = s?.used != null && s.total ? (s.used / s.total) * 100 : null;
            const ino = m.resources?.inodes;
            return (
              <tr key={m.member}>
                <td>
                  <MemberLink name={m.member} />
                </td>
                <td>{m.error ? <span role="alert">{m.error}</span> : m.status || "–"}</td>
                <td className="num">{s?.used != null ? formatBytes(s.used) : "–"}</td>
                <td className="num">{s ? formatBytes(s.total) : "–"}</td>
                <td>
                  {pct != null ? (
                    <>
                      <meter min={0} max={100} high={85} value={pct} aria-label={`${m.member} used`} /> {formatPercent(pct)}
                    </>
                  ) : (
                    "–"
                  )}
                </td>
                <td className="num">{ino && ino.total ? `${formatNumber(ino.used)} / ${formatNumber(ino.total)}` : "–"}</td>
                <td>{m.config.source ?? "–"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
