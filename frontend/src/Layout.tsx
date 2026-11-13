import { NavLink, Outlet } from "react-router";
import { fetchProjects } from "./api";
import { useProject } from "./project";
import { applyTheme, THEMES, useThemeChoice, type Theme } from "./theme";
import { useSession } from "./session";
import { useApi } from "./useApi";
import { useAccess } from "./access";

// who sees each page: everyone, a global role ("cluster"), or a cluster admin (D81)
const NAV: { to: string; label: string; end?: boolean; who?: "cluster" | "admin" }[] = [
  { to: "/", label: "Overview", end: true },
  { to: "/instances", label: "Instances" },
  { to: "/profiles", label: "Profiles" },
  { to: "/images", label: "Images" },
  { to: "/projects", label: "Projects" },
  { to: "/storage", label: "Storage", who: "cluster" },
  { to: "/ceph", label: "Ceph", who: "cluster" },
  { to: "/networks", label: "Networks" },
  { to: "/operations", label: "Operations" },
  { to: "/warnings", label: "Warnings", who: "cluster" },
  { to: "/users", label: "Users", who: "admin" },
];

export default function Layout() {
  const { user, onLogout } = useSession();
  const access = useAccess();
  const { project, setProject } = useProject();
  const projects = useApi("projects", fetchProjects, 300_000);
  return (
    <>
      <header className="topbar">
        <strong>microcloud-ui</strong>
        <nav aria-label="Main">
          {NAV.filter((n) => !n.who || (n.who === "cluster" ? access.cluster : access.clusterAdmin)).map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end}>
              {n.label}
            </NavLink>
          ))}
        </nav>
        <label className="project">
          Project{" "}
          <select value={project} onChange={(e) => setProject(e.target.value)}>
            <option value="">All projects</option>
            {(projects.data ?? []).map((p) => (
              <option key={p.name} value={p.name}>
                {p.name}
              </option>
            ))}
            {project && !(projects.data ?? []).some((p) => p.name === project) && (
              <option value={project}>{project}</option>
            )}
          </select>
        </label>
        <ThemeSelect />
        <span>
          {user.username} ({user.role}){" "}
          <button type="button" onClick={onLogout}>
            Sign out
          </button>
        </span>
      </header>
      <main>
        <Outlet />
      </main>
    </>
  );
}

const THEME_LABELS: Record<Theme, string> = { system: "System", light: "Light", dark: "Dark" };

function ThemeSelect() {
  const theme = useThemeChoice();
  return (
    <label className="project">
      Theme{" "}
      <select value={theme} onChange={(e) => applyTheme(e.target.value as Theme)}>
        {THEMES.map((t) => (
          <option key={t} value={t}>
            {THEME_LABELS[t]}
          </option>
        ))}
      </select>
    </label>
  );
}
