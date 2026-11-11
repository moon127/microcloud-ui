import type { AclRule } from "../../api";
import { emptyRule } from "./request";

const TEXT: { key: keyof AclRule; label: string; ports?: boolean; icmp?: boolean }[] = [
  { key: "source", label: "Source" },
  { key: "destination", label: "Destination" },
  { key: "source_port", label: "Source port", ports: true },
  { key: "destination_port", label: "Destination port", ports: true },
  { key: "icmp_type", label: "ICMP type", icmp: true },
  { key: "icmp_code", label: "ICMP code", icmp: true },
  { key: "description", label: "Description" },
];

/** One direction's rules as an editable table; ports only for tcp/udp, ICMP fields only for icmp. */
export default function RulesEditor({
  label,
  rules,
  readOnly,
  onChange,
}: {
  label: string;
  rules: AclRule[];
  readOnly: boolean;
  onChange: (rules: AclRule[]) => void;
}) {
  const set = (i: number, patch: Partial<AclRule>) => onChange(rules.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  return (
    <section aria-label={label}>
      <h2>{label}</h2>
      {rules.length === 0 && <p className="muted">No rules: the network's default action applies.</p>}
      {rules.length > 0 && (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>Action</th>
                <th>State</th>
                <th>Protocol</th>
                {TEXT.map((t) => (
                  <th key={t.key}>{t.label}</th>
                ))}
                <th />
              </tr>
            </thead>
            <tbody>
              {rules.map((r, i) => {
                const n = `${label} rule ${i + 1}`;
                const ports = r.protocol === "tcp" || r.protocol === "udp";
                const icmp = r.protocol === "icmp4" || r.protocol === "icmp6";
                return (
                  <tr key={i}>
                    <td>
                      <select aria-label={`${n} action`} disabled={readOnly} value={r.action} onChange={(e) => set(i, { action: e.target.value as AclRule["action"] })}>
                        <option>allow</option>
                        <option>allow-stateless</option>
                        <option>drop</option>
                        <option>reject</option>
                      </select>
                    </td>
                    <td>
                      <select aria-label={`${n} state`} disabled={readOnly} value={r.state} onChange={(e) => set(i, { state: e.target.value as AclRule["state"] })}>
                        <option>enabled</option>
                        <option>disabled</option>
                        <option>logged</option>
                      </select>
                    </td>
                    <td>
                      <select
                        aria-label={`${n} protocol`}
                        disabled={readOnly}
                        value={r.protocol}
                        onChange={(e) => {
                          const protocol = e.target.value as AclRule["protocol"];
                          const p = protocol === "tcp" || protocol === "udp";
                          const ic = protocol === "icmp4" || protocol === "icmp6";
                          // drop fields LXD rejects for the new protocol
                          set(i, {
                            protocol,
                            ...(p ? {} : { source_port: "", destination_port: "" }),
                            ...(ic ? {} : { icmp_type: "", icmp_code: "" }),
                          });
                        }}
                      >
                        <option value="">any</option>
                        <option>tcp</option>
                        <option>udp</option>
                        <option>icmp4</option>
                        <option>icmp6</option>
                      </select>
                    </td>
                    {TEXT.map((t) => (
                      <td key={t.key}>
                        <input
                          aria-label={`${n} ${t.label.toLowerCase()}`}
                          size={t.key === "description" || t.key === "source" || t.key === "destination" ? 14 : 6}
                          readOnly={readOnly}
                          disabled={(t.ports && !ports) || (t.icmp && !icmp)}
                          value={r[t.key]}
                          onChange={(e) => set(i, { [t.key]: e.target.value })}
                        />
                      </td>
                    ))}
                    <td>
                      {!readOnly && (
                        <button type="button" aria-label={`Remove ${n}`} onClick={() => onChange(rules.filter((_, j) => j !== i))}>
                          ×
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {!readOnly && (
        <button type="button" onClick={() => onChange([...rules, emptyRule()])}>
          Add {label.toLowerCase()} rule
        </button>
      )}
    </section>
  );
}
