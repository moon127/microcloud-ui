import { useCallback, useEffect, useMemo, useState } from "react";
import { BrowserRouter, Route, Routes } from "react-router";
import { ApiError, fetchMe, logout, setCsrf, type User } from "./api";
import ActionsProvider from "./ActionsProvider";
import Layout from "./Layout";
import Login from "./Login";
import CreateInstancePage from "./pages/create/CreateInstancePage";
import InstancePage from "./pages/instance/InstancePage";
import InstancesPage from "./pages/InstancesPage";
import MemberPage from "./pages/MemberPage";
import NetworksPage from "./pages/NetworksPage";
import OperationsPage from "./pages/OperationsPage";
import Overview from "./pages/Overview";
import StoragePage from "./pages/StoragePage";
import WarningsPage from "./pages/WarningsPage";
import { ProjectProvider } from "./ProjectProvider";
import { SessionContext } from "./session";
import "./index.css";

type State =
  | { kind: "loading" }
  | { kind: "anonymous" }
  | { kind: "user"; user: User }
  | { kind: "error"; message: string };

export function AppRoutes() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Overview />} />
        <Route path="instances" element={<InstancesPage />} />
        <Route path="instances/new" element={<CreateInstancePage />} />
        <Route path="instances/:project/:name" element={<InstancePage />} />
        <Route path="members/:name" element={<MemberPage />} />
        <Route path="storage" element={<StoragePage />} />
        <Route path="networks" element={<NetworksPage />} />
        <Route path="operations" element={<OperationsPage />} />
        <Route path="warnings" element={<WarningsPage />} />
        <Route path="*" element={<p>Page not found.</p>} />
      </Route>
    </Routes>
  );
}

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

  const user = state.kind === "user" ? state.user : null;
  const session = useMemo(
    () => (user ? { user, onExpired: expired, onLogout: signOut } : null),
    [user, expired, signOut],
  );

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
      return (
        <SessionContext.Provider value={session}>
          <ProjectProvider>
            <ActionsProvider>
              <BrowserRouter>
                <AppRoutes />
              </BrowserRouter>
            </ActionsProvider>
          </ProjectProvider>
        </SessionContext.Provider>
      );
  }
}
