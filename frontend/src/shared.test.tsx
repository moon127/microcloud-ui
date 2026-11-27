import { screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "./api";
import ProfilesPage from "./pages/manage/ProfilesPage";
import VolumesPage from "./pages/storage/VolumesPage";
import { renderAt, session } from "./test/render";

afterEach(() => vi.restoreAllMocks());

const pat = session({ user: { username: "p", role: "project", grants: { web: "admin" } } });
const project = (config: Record<string, string>): api.Project => ({ name: "web", description: "", config, used_by: [] });

describe("objects shared with the default project (D89)", () => {
  it("a project admin isn't offered writes to default's profiles, with the reason", async () => {
    vi.spyOn(api, "fetchProfiles").mockResolvedValue([]);
    vi.spyOn(api, "fetchProject").mockResolvedValue(project({ restricted: "true" }));
    const a = renderAt(<ProfilesPage />, { s: pat, url: "/profiles?project=web" });
    expect(await screen.findByText("web shares the default project's profiles, so only a cluster admin can change them.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create profile" })).toBeNull();
    a.unmount();
    vi.spyOn(api, "fetchProject").mockResolvedValue(project({ restricted: "true", "features.profiles": "true" }));
    const b = renderAt(<ProfilesPage />, { s: pat, url: "/profiles?project=web" });
    expect(await screen.findByRole("button", { name: "Create profile" })).toBeInTheDocument();
    expect(screen.queryByText(/shares the default project/)).toBeNull();
    b.unmount();
  });

  it("cluster admins aren't limited, and the project isn't even fetched", async () => {
    vi.spyOn(api, "fetchVolumes").mockResolvedValue([]);
    const get = vi.spyOn(api, "fetchProject");
    renderAt(<VolumesPage />, { url: "/storage/volumes?project=web" });
    expect(await screen.findByRole("button", { name: "Create volume" })).toBeInTheDocument();
    expect(get).not.toHaveBeenCalled();
  });

  it("a project admin's volumes in a sharing project: no Create, a note", async () => {
    vi.spyOn(api, "fetchVolumes").mockResolvedValue([]);
    vi.spyOn(api, "fetchProject").mockResolvedValue(project({ restricted: "true" }));
    renderAt(<VolumesPage />, { s: pat, url: "/storage/volumes?project=web" });
    await waitFor(() => expect(screen.getByText(/shares the default project's storage volumes/)).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Create volume" })).toBeNull();
  });
});

describe("shared objects, edge cases (D90)", () => {
  it("a failed project fetch says nothing about sharing, and offers nothing", async () => {
    vi.spyOn(api, "fetchProfiles").mockResolvedValue([]);
    vi.spyOn(api, "fetchProject").mockRejectedValue(new Error("boom"));
    renderAt(<ProfilesPage />, { s: pat, url: "/profiles?project=web" });
    await screen.findByRole("heading", { name: /Profiles in web/ });
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText(/shares the default project/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Create profile" })).toBeNull();
  });
});
