import { NavLink, Outlet } from "react-router";
import { useSession } from "./session";

const NAV = [
  { to: "/", label: "Overview", end: true },
  { to: "/instances", label: "Instances" },
  { to: "/storage", label: "Storage" },
  { to: "/networks", label: "Networks" },
];

export default function Layout() {
  const { user, onLogout } = useSession();
  return (
    <>
      <header className="topbar">
        <strong>microcloud-ui</strong>
        <nav aria-label="Main">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end}>
              {n.label}
            </NavLink>
          ))}
        </nav>
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
