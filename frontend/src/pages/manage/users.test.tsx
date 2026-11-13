import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { renderAt, session } from "../../test/render";
import UsersPage from "./UsersPage";

afterEach(() => vi.restoreAllMocks());

const users: api.User[] = [
  { username: "alice", role: "admin", grants: {} },
  { username: "vic", role: "viewer", grants: {} },
  { username: "pat", role: "project", grants: { web: "operator" } },
  { username: "new", role: "project", grants: {} },
];
const projects: api.Project[] = [
  { name: "web", description: "", config: { restricted: "true" }, used_by: [] },
  { name: "open", description: "", config: {}, used_by: [] },
];
const dialog = () => within(screen.getByRole("dialog"));
const row = (name: string) => screen.getByText(name, { selector: "td" }).closest("tr")!;

describe("UsersPage (D81)", () => {
  it("lists users with their role and projects; only cluster admins see it", async () => {
    vi.spyOn(api, "fetchUsers").mockResolvedValue(users);
    renderAt(<UsersPage />);
    expect(await screen.findByText("pat")).toBeInTheDocument();
    expect(row("pat")).toHaveTextContent("project userweb: operator");
    expect(row("new")).toHaveTextContent("none yet");
    expect(row("vic")).toHaveTextContent("cluster viewerall");
    // no Projects button for a global role; no deleting yourself
    expect(within(row("vic")).queryByRole("button", { name: "Projects" })).toBeNull();
    expect(within(row("alice")).queryByRole("button", { name: "Delete" })).toBeNull();
  });

  it("refuses anyone but a cluster admin", () => {
    renderAt(<UsersPage />, { s: session({ user: { username: "v", role: "viewer" } }) });
    expect(screen.getByRole("alert")).toHaveTextContent("needs the cluster admin role");
  });

  it("creates, edits and deletes users", async () => {
    vi.spyOn(api, "fetchUsers").mockResolvedValue(users);
    const create = vi.spyOn(api, "createUser").mockResolvedValue(users[2]);
    const update = vi.spyOn(api, "updateUser").mockResolvedValue(users[2]);
    const del = vi.spyOn(api, "deleteUser").mockResolvedValue();
    renderAt(<UsersPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Create user" }));
    fireEvent.click(dialog().getByRole("button", { name: "Create" }));
    expect(dialog().getByRole("alert")).toHaveTextContent("Enter a username and a password");
    fireEvent.change(dialog().getByLabelText("Username"), { target: { value: " bob " } });
    fireEvent.change(dialog().getByLabelText("Password"), { target: { value: "pw" } });
    expect(dialog().getByText("Grant projects once the user exists.")).toBeInTheDocument();
    fireEvent.click(dialog().getByRole("button", { name: "Create" }));
    expect(create).toHaveBeenCalledWith("bob", "pw", "project");
    expect(await screen.findByText(/✓ Create user bob/)).toBeInTheDocument();

    fireEvent.click(within(row("pat")).getByRole("button", { name: "Edit" }));
    fireEvent.change(dialog().getByLabelText("Role"), { target: { value: "viewer" } });
    expect(dialog().getByText(/grants no longer apply/)).toBeInTheDocument();
    fireEvent.change(dialog().getByLabelText(/New password/), { target: { value: "s3cret" } });
    fireEvent.click(dialog().getByRole("button", { name: "Save" }));
    expect(update).toHaveBeenCalledWith("pat", { role: "viewer", password: "s3cret" });
    // nothing changed: nothing sent
    fireEvent.click(within(row("vic")).getByRole("button", { name: "Edit" }));
    fireEvent.click(dialog().getByRole("button", { name: "Save" }));
    expect(update).toHaveBeenCalledTimes(1);

    fireEvent.click(within(row("vic")).getByRole("button", { name: "Delete" }));
    fireEvent.click(dialog().getByRole("button", { name: "Delete" }));
    expect(del).toHaveBeenCalledWith("vic");
  });

  it("grants projects, warning that operator and admin need a restricted one", async () => {
    vi.spyOn(api, "fetchUsers").mockResolvedValue(users);
    vi.spyOn(api, "fetchProjects").mockResolvedValue(projects);
    const grant = vi.spyOn(api, "setGrant").mockResolvedValue(users[2]);
    const remove = vi.spyOn(api, "removeGrant").mockResolvedValue(users[2]);
    renderAt(<UsersPage />);
    fireEvent.click(within(await waitFor(() => row("pat"))).getByRole("button", { name: "Projects" }));
    await dialog().findByRole("option", { name: "web (restricted)" });
    fireEvent.click(dialog().getByRole("button", { name: "Remove" }));
    expect(remove).toHaveBeenCalledWith("pat", "web");
    expect(dialog().getByRole("button", { name: "Grant" })).toBeDisabled();
    fireEvent.change(dialog().getByLabelText("Project"), { target: { value: "open" } });
    fireEvent.change(dialog().getByLabelText("Role"), { target: { value: "admin" } });
    expect(dialog().getByRole("alert")).toHaveTextContent("open isn't restricted, so only a viewer grant is allowed");
    fireEvent.change(dialog().getByLabelText("Project"), { target: { value: "web" } });
    expect(dialog().queryByRole("alert")).toBeNull();
    fireEvent.click(dialog().getByRole("button", { name: "Grant" }));
    expect(grant).toHaveBeenCalledWith("pat", "web", "admin");
    fireEvent.click(dialog().getByRole("button", { name: "Done" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    // a project user with nothing yet
    fireEvent.click(within(row("new")).getByRole("button", { name: "Projects" }));
    expect(dialog().getByText("No projects yet.")).toBeInTheDocument();
  });
});
