import { useState } from "react";
import { updateInstance, type InstanceFull, type InstanceUpdate } from "../../api";
import { useActions } from "../../actions";
import YamlEditor from "../../YamlEditor";
import { asConfig, asDevices } from "./state";
import { useAccess } from "../../access";

function draftOf(inst: InstanceFull): InstanceUpdate {
  return {
    description: inst.description ?? "",
    config: { ...inst.config },
    devices: structuredClone(inst.devices ?? {}),
    profiles: [...(inst.profiles ?? [])],
    ephemeral: inst.ephemeral ?? false,
    stateful: inst.stateful ?? false,
    ...(inst.architecture ? { architecture: inst.architecture } : {}),
  };
}

const TEXT_KEYS: { key: string; label: string; hint: string }[] = [
  { key: "limits.cpu", label: "CPU limit", hint: "e.g. 2, or 0-3 to pin" },
  { key: "limits.memory", label: "Memory limit", hint: "e.g. 2GiB or 50%" },
];

const BOOL_KEYS: { key: string; label: string }[] = [
  { key: "boot.autostart", label: "Start on host boot" },
  { key: "security.nesting", label: "Allow nesting (containers in it)" },
  { key: "security.privileged", label: "Privileged container" },
];

export default function ConfigTab({ inst }: { inst: InstanceFull }) {
  const access = useAccess();
  const { run } = useActions();
  const [draft, setDraft] = useState(() => draftOf(inst));
  const [problem, setProblem] = useState<string | null>(null);
  const readOnly = !access.can(inst.project, "manage");
  const changed = JSON.stringify(draft) !== JSON.stringify(draftOf(inst));

  const setKey = (key: string, value: string) =>
    setDraft((d) => {
      const config = { ...d.config };
      if (value === "") delete config[key];
      else config[key] = value;
      return { ...d, config };
    });

  return (
    <section aria-label="Configuration" className="config">
      <fieldset disabled={readOnly}>
        <label className="field">
          Description
          <input value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} />
        </label>
        <label className="field">
          Profiles (in order, comma separated)
          <input
            value={draft.profiles.join(", ")}
            onChange={(e) =>
              setDraft({
                ...draft,
                profiles: e.target.value
                  .split(",")
                  .map((p) => p.trim())
                  .filter(Boolean),
              })
            }
          />
        </label>
        {TEXT_KEYS.map((k) => (
          <label key={k.key} className="field">
            {k.label} <span className="muted">({k.key}; {k.hint})</span>
            <input value={draft.config[k.key] ?? ""} onChange={(e) => setKey(k.key, e.target.value)} />
          </label>
        ))}
        {BOOL_KEYS.map((k) => (
          <label key={k.key}>
            <input
              type="checkbox"
              checked={draft.config[k.key] === "true"}
              onChange={(e) => setKey(k.key, e.target.checked ? "true" : "")}
            />{" "}
            {k.label} <span className="muted">({k.key})</span>
          </label>
        ))}
      </fieldset>
      <div className="grid">
        <YamlEditor
          label="Instance config (YAML)"
          value={draft.config}
          readOnly={readOnly}
          onChange={(v) => {
            const c = asConfig(v);
            if (typeof c === "string") setProblem(c);
            else {
              setProblem(null);
              setDraft((d) => ({ ...d, config: c }));
            }
          }}
        />
        <YamlEditor
          label="Instance devices (YAML)"
          value={draft.devices}
          readOnly={readOnly}
          onChange={(v) => {
            const d = asDevices(v);
            if (typeof d === "string") setProblem(d);
            else {
              setProblem(null);
              setDraft((x) => ({ ...x, devices: d }));
            }
          }}
        />
      </div>
      {problem && <p role="alert">{problem}</p>}
      <details>
        <summary>Effective devices, including those from profiles</summary>
        <YamlEditor label="Expanded devices (read-only)" value={inst.expanded_devices ?? {}} readOnly onChange={() => {}} rows={10} />
      </details>
      {!readOnly && (
        <div className="actions">
          <button
            type="button"
            className="primary"
            disabled={!changed || problem !== null}
            onClick={() => void run(`Save ${inst.name} configuration`, () => updateInstance(inst.project, inst.name, draft))}
          >
            Save
          </button>
          <button type="button" disabled={!changed} onClick={() => setDraft(draftOf(inst))}>
            Discard changes
          </button>
        </div>
      )}
    </section>
  );
}
