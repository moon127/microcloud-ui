import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router";
import { vi } from "vitest";
import ActionsProvider from "../ActionsProvider";
import { ProjectProvider } from "../ProjectProvider";
import { SessionContext, type Session } from "../session";

export function session(over: Partial<Session> = {}): Session {
  return { user: { username: "alice", role: "admin" }, onExpired: vi.fn(), onLogout: vi.fn(), ...over };
}

/** Render ``ui`` at ``url`` inside a router (matching ``path``) and a session. */
export function renderAt(ui: ReactNode, { url = "/", path = "*", s = session() } = {}) {
  return {
    session: s,
    ...render(
      <SessionContext.Provider value={s}>
        <ProjectProvider>
          <ActionsProvider>
            <MemoryRouter initialEntries={[url]}>
              <Routes>
                <Route path={path} element={ui} />
              </Routes>
            </MemoryRouter>
          </ActionsProvider>
        </ProjectProvider>
      </SessionContext.Provider>,
    ),
  };
}
