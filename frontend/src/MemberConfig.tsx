import { useState } from "react";
import { asConfig } from "./pages/instance/state";
import YamlEditor from "./YamlEditor";

/** One cluster member's own settings of a network or pool, as YAML with its own Save. */
export default function MemberConfig({
  member,
  config: initial,
  error,
  admin,
  onSave,
}: {
  member: string;
  config: Record<string, string>;
  error?: string;
  admin: boolean;
  onSave: (c: Record<string, string>) => void;
}) {
  const [config, setConfig] = useState(initial);
  const [problem, setProblem] = useState<string | null>(null);
  if (error)
    return (
      <p role="alert">
        {member}: {error}
      </p>
    );
  return (
    <div>
      <YamlEditor
        label={member}
        rows={3}
        value={config}
        readOnly={!admin}
        onChange={(v) => {
          const c = asConfig(v);
          setProblem(typeof c === "string" ? c : null);
          if (typeof c !== "string") setConfig(c);
        }}
      />
      {problem && <p role="alert">{problem}</p>}
      {admin && (
        <button type="button" disabled={problem !== null} onClick={() => onSave(config)}>
          Save {member}
        </button>
      )}
    </div>
  );
}
