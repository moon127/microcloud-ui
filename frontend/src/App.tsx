import { useCallback, useEffect, useState } from "react";
import { ApiError, fetchMe, logout, setCsrf, type User } from "./api";
import Dashboard from "./Dashboard";
import Login from "./Login";
import "./index.css";

type State =
  | { kind: "loading" }
  | { kind: "anonymous" }
  | { kind: "user"; user: User }
  | { kind: "error"; message: string };

export default function App() {
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    let active = true;
    fetchMe()
      .then((me) => active && setState({ kind: "user", user: me.user }))
      .catch((e: unknown) => {
        if (!active) return;
        if (e instanceof ApiError && e.status === 401) setState({ kind: "anonymous" });
        else setState({ kind: "error", message: e instanceof Error ? e.message : String(e) });
      });
    return () => {
      active = false;
    };
  }, []);

  const expired = useCallback(() => {
    setCsrf(null);
    setState({ kind: "anonymous" });
  }, []);

  const signOut = useCallback(() => {
    // the session is dropped locally even if the server call fails
    logout()
      .catch(() => undefined)
      .then(expired);
  }, [expired]);

  switch (state.kind) {
    case "loading":
      return (
        <main>
          <p role="status">Checking session…</p>
        </main>
      );
    case "error":
      return (
        <main>
          <h1>microcloud-ui</h1>
          <p role="alert">Backend unreachable: {state.message}</p>
        </main>
      );
    case "anonymous":
      return (
        <main>
          <Login onLogin={(me) => setState({ kind: "user", user: me.user })} />
        </main>
      );
    case "user":
      return <Dashboard user={state.user} onLogout={signOut} onExpired={expired} />;
  }
}
