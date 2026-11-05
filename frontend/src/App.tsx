import { useEffect, useState } from "react";
import { fetchHealth, type Health } from "./api";
import "./index.css";

type State =
  | { kind: "loading" }
  | { kind: "ok"; health: Health }
  | { kind: "error"; message: string };

export default function App() {
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    let active = true;
    fetchHealth()
      .then((health) => active && setState({ kind: "ok", health }))
      .catch((e: unknown) =>
        active && setState({ kind: "error", message: e instanceof Error ? e.message : String(e) }),
      );
    return () => {
      active = false;
    };
  }, []);

  return (
    <main>
      <h1>microcloud-ui</h1>
      <p role="status">
        {state.kind === "loading" && "Checking backend…"}
        {state.kind === "ok" && `Backend ${state.health.status} (v${state.health.version})`}
        {state.kind === "error" && `Backend unreachable: ${state.message}`}
      </p>
    </main>
  );
}
