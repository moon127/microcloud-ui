import { useSearchParams } from "react-router";
import { RANGES } from "./format";

const DEFAULT_RANGE = 3600;

/** The selected time range in seconds, from ``?range=`` (default 1 h). */
export function useRange(): number {
  const [params] = useSearchParams();
  const r = Number(params.get("range"));
  return RANGES.some((x) => x.seconds === r) ? r : DEFAULT_RANGE;
}
