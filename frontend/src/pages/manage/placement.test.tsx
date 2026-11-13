import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { renderAt, session } from "../../test/render";
import PlacementGroupsPage from "./PlacementGroupsPage";

const server = (placement_groups: boolean, version = "6.10.1"): api.ServerInfo => ({
  version,
  clustered: true,
  server_name: "m1",
  auth: "trusted",
  auth_method: "tls",
  endpoint: "",
  features: { placement_groups },
});

// as recorded from LXD 6.10 (tests/fixtures/recorded-6.10)
const groups: api.PlacementGroup[] = [
  {
    name: "pg-spread",
    description: "mcui test: spread",
    project: "default",
    config: { policy: "spread", rigor: "permissive" },
    used_by: ["/1.0/instances/c2", "/1.0/instances/c3?project=default"],
  },
  { name: "packed", description: "", project: "default", config: { policy: "compact" }, used_by: [] },
];

afterEach(() => vi.restoreAllMocks());

describe("PlacementGroupsPage", () => {
  it("lists groups with their instances; admins create, edit and delete", async () => {
    vi.spyOn(api, "fetchServer").mockResolvedValue(server(true));
    vi.spyOn(api, "fetchPlacementGroups").mockResolvedValue(groups);
    const create = vi.spyOn(api, "createPlacementGroup").mockResolvedValue({ operation: null, metadata: null });
    const update = vi.spyOn(api, "updatePlacementGroup").mockResolvedValue({ operation: null, metadata: null });
    const del = vi.spyOn(api, "deletePlacementGroup").mockResolvedValue({ operation: null, metadata: null });
    renderAt(<PlacementGroupsPage />);
    const row = (await screen.findByText("pg-spread")).closest("tr")!;
    expect(row).toHaveTextContent("spreadpermissivemcui test: spread");
    expect(within(row).getByRole("link", { name: "c3" })).toHaveAttribute("href", "/instances/default/c3");
    expect(screen.getByText("packed").closest("tr")).toHaveTextContent("compact–none");
    // checked live on LXD 6.10: a move to a named member ignores a strict spread group
    expect(screen.getByText(/moving an instance to a named member isn.t checked/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Create placement group" }));
    let dlg = screen.getByRole("dialog", { name: "New placement group in default" });
    fireEvent.change(within(dlg).getByLabelText("Name"), { target: { value: "web" } });
    fireEvent.change(within(dlg).getByLabelText(/Policy/), { target: { value: "compact" } });
    fireEvent.change(within(dlg).getByLabelText(/Rigor/), { target: { value: "permissive" } });
    fireEvent.click(within(dlg).getByRole("button", { name: "Create" }));
    expect(create).toHaveBeenCalledWith("default", "web", { description: "", policy: "compact", rigor: "permissive" });

    fireEvent.click(within(row).getByRole("button", { name: "Edit" }));
    dlg = screen.getByRole("dialog", { name: "Edit pg-spread" });
    expect(within(dlg).queryByLabelText("Name")).toBeNull();
    fireEvent.change(within(dlg).getByLabelText(/Description/), { target: { value: "web tier" } });
    fireEvent.click(within(dlg).getByRole("button", { name: "Save" }));
    expect(update).toHaveBeenCalledWith("default", "pg-spread", { description: "web tier", policy: "spread", rigor: "permissive" });

    fireEvent.click(within(row).getByRole("button", { name: "Delete" }));
    dlg = screen.getByRole("dialog", { name: "Delete placement group pg-spread?" });
    expect(dlg).toHaveTextContent("LXD refuses while instances still use it");
    fireEvent.click(within(dlg).getByRole("button", { name: "Delete" }));
    expect(del).toHaveBeenCalledWith("default", "pg-spread");
  });

  it("needs a name to create; viewers only read", async () => {
    vi.spyOn(api, "fetchServer").mockResolvedValue(server(true));
    vi.spyOn(api, "fetchPlacementGroups").mockResolvedValue([]);
    const create = vi.spyOn(api, "createPlacementGroup");
    const { unmount } = renderAt(<PlacementGroupsPage />);
    expect(await screen.findByText("No placement groups in default.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Create placement group" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Create" }));
    expect(create).not.toHaveBeenCalled();
    unmount();
    vi.spyOn(api, "fetchPlacementGroups").mockResolvedValue(groups);
    renderAt(<PlacementGroupsPage />, { s: session({ user: { username: "v", role: "viewer" } }) });
    expect(await screen.findByText("pg-spread")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Create|Edit|Delete/ })).toBeNull();
  });

  it("explains that LXD before 6.6 has no placement groups", async () => {
    vi.spyOn(api, "fetchServer").mockResolvedValue(server(false, "5.21.9"));
    const list = vi.spyOn(api, "fetchPlacementGroups");
    renderAt(<PlacementGroupsPage />);
    expect(await screen.findByText(/need LXD 6.6 or later/)).toHaveTextContent("this cluster runs LXD 5.21.9");
    expect(list).not.toHaveBeenCalled();
  });

  it("shows a load error", async () => {
    vi.spyOn(api, "fetchServer").mockResolvedValue(server(true));
    vi.spyOn(api, "fetchPlacementGroups").mockRejectedValue(new Error("LXD down"));
    renderAt(<PlacementGroupsPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("LXD down");
  });
});
