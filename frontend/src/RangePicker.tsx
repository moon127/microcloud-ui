import { useSearchParams } from "react-router";
import { RANGES } from "./format";
import { useRange } from "./range";

export default function RangePicker() {
  const [params, setParams] = useSearchParams();
  const range = useRange();
  return (
    <div className="ranges" role="group" aria-label="Time range">
      {RANGES.map((r) => (
        <button
          key={r.label}
          type="button"
          aria-pressed={r.seconds === range}
          onClick={() => {
            const next = new URLSearchParams(params);
            next.set("range", String(r.seconds));
            setParams(next);
          }}
        >
          {r.label}
        </button>
      ))}
    </div>
  );
}
