import { useEffect, useState } from "react";
import { parse, stringify } from "yaml";

interface Props {
  label: string;
  value: unknown;
  onChange: (value: unknown) => void;
  rows?: number;
  readOnly?: boolean;
}

/** Edit any JSON-able value as YAML; ``onChange`` fires only with valid YAML. */
export default function YamlEditor({ label, value, onChange, rows = 18, readOnly }: Props) {
  const [text, setText] = useState(() => stringify(value));
  const [error, setError] = useState<string | null>(null);

  // follow external changes (e.g. the form tab edited the same object)
  useEffect(() => {
    setText((t) => {
      try {
        return JSON.stringify(parse(t)) === JSON.stringify(value) ? t : stringify(value);
      } catch {
        return stringify(value);
      }
    });
  }, [value]);

  return (
    <label className="yaml">
      {label}
      <textarea
        value={text}
        rows={rows}
        readOnly={readOnly}
        spellCheck={false}
        aria-invalid={error !== null}
        onChange={(e) => {
          setText(e.target.value);
          try {
            const v: unknown = parse(e.target.value);
            setError(null);
            onChange(v);
          } catch (err) {
            setError(err instanceof Error ? err.message.split("\n")[0] : String(err));
          }
        }}
      />
      {error && <span role="alert">{error}</span>}
    </label>
  );
}
