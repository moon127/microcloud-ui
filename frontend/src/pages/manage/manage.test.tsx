import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { renderAt, session } from "../../test/render";
import ImagesPage from "./ImagesPage";
import ProfilePage from "./ProfilePage";
import ProfilesPage from "./ProfilesPage";
import ProjectPage from "./ProjectPage";
import ProjectsPage from "./ProjectsPage";

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.removeItem("mcui.project");
});

const ok: api.Started = { operation: null, metadata: null };
const viewer = () => session({ user: { username: "v", role: "viewer" } });
const dialog = () => within(screen.getByRole("dialog"));

function routes(url: string, s = session()) {
  return renderAt(
    <Routes>
      <Route path="/profiles" element={<ProfilesPage />} />
      <Route path="/profiles/:project/:name" element={<ProfilePage />} />
      <Route path="/projects" element={<ProjectsPage />} />
      <Route path="/projects/:name" element={<ProjectPage />} />
      <Route path="/images" element={<ImagesPage />} />
    </Routes>,
    { url, s },
  );
}

const prof = (over: Partial<api.ProfileFull> = {}): api.ProfileFull => ({
  name: "web",
  description: "web servers",
  config: { "limits.cpu": "2" },
  devices: { root: { type: "disk", path: "/", pool: "remote" } },
  used_by: ["/1.0/instances/a", "/1.0/instances/b?project=lab", "/1.0/profiles/x", "/1.0/storage-pools/p"],
  project: "default",
  ...over,
});

describe("profiles", () => {
  it("lists profiles of the selected project and creates one", async () => {
    localStorage.setItem("mcui.project", "lab");
    vi.spyOn(api, "fetchProfiles").mockResolvedValue([prof({ project: "lab" }), prof({ name: "empty", devices: {}, used_by: [] })]);
    vi.spyOn(api, "fetchProfile").mockResolvedValue(prof({ name: "new", used_by: [] }));
    const create = vi.spyOn(api, "createProfile").mockResolvedValue(ok);
    routes("/profiles");
    expect(await screen.findByRole("heading", { name: "Profiles in lab" })).toBeInTheDocument();
    const row = screen.getByRole("link", { name: "web" }).closest("tr")!;
    expect(row).toHaveTextContent("root");
    expect(row).toHaveTextContent("4");
    expect(screen.getByRole("link", { name: "web" })).toHaveAttribute("href", "/profiles/lab/web");
    fireEvent.click(screen.getByRole("button", { name: "Create profile" }));
    fireEvent.change(dialog().getByLabelText("Profile name"), { target: { value: "new" } });
    fireEvent.click(dialog().getByRole("button", { name: "OK" }));
    expect(create).toHaveBeenCalledWith("lab", "new", { description: "", config: {}, devices: {} });
    expect(await screen.findByRole("heading", { name: /Profile new/ })).toBeInTheDocument();
  });

  it("list states: viewer, cancel, error", async () => {
    vi.spyOn(api, "fetchProfiles").mockResolvedValue([]);
    const a = routes("/profiles", viewer());
    expect(await screen.findByRole("heading", { name: "Profiles in default" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create profile" })).toBeNull();
    a.unmount();
    const b = routes("/profiles");
    fireEvent.click(await screen.findByRole("button", { name: "Create profile" }));
    fireEvent.click(dialog().getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    b.unmount();
    vi.spyOn(api, "fetchProfiles").mockRejectedValue(new Error("down"));
    routes("/profiles");
    expect(await screen.findByRole("alert")).toHaveTextContent("down");
  });

  it("edits, copies, renames and deletes a profile", async () => {
    vi.spyOn(api, "fetchProfile").mockResolvedValue(prof());
    vi.spyOn(api, "fetchProfiles").mockResolvedValue([]);
    const upd = vi.spyOn(api, "updateProfile").mockResolvedValue(ok);
    const create = vi.spyOn(api, "createProfile").mockResolvedValue(ok);
    const ren = vi.spyOn(api, "renameProfile").mockResolvedValue(ok);
    const del = vi.spyOn(api, "deleteProfile").mockResolvedValue(ok);
    routes("/profiles/default/web");
    expect(await screen.findByRole("link", { name: "a" })).toHaveAttribute("href", "/instances/default/a");
    expect(screen.getByRole("link", { name: "lab/b" })).toHaveAttribute("href", "/instances/lab/b");
    expect(screen.getByRole("link", { name: "profile x" })).toHaveAttribute("href", "/profiles/default/x");
    expect(screen.getByText("/1.0/storage-pools/p")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "edited" } });
    fireEvent.change(screen.getByLabelText("Config (YAML)"), { target: { value: "limits.memory: 1GiB" } });
    fireEvent.change(screen.getByLabelText("Devices (YAML)"), { target: { value: "eth0: {name: x}" } });
    expect(screen.getByRole("alert")).toHaveTextContent("every device needs a type");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Devices (YAML)"), { target: { value: "{}" } });
    fireEvent.change(screen.getByLabelText("Config (YAML)"), { target: { value: "[1]" } });
    expect(screen.getByRole("alert")).toHaveTextContent("mapping");
    fireEvent.change(screen.getByLabelText("Config (YAML)"), { target: { value: "limits.memory: 1GiB" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(upd).toHaveBeenCalledWith("default", "web", { description: "edited", config: { "limits.memory": "1GiB" }, devices: {} });

    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    fireEvent.click(dialog().getByRole("button", { name: "OK" }));
    expect(create).toHaveBeenCalledWith("default", "web-copy", expect.objectContaining({ description: "edited" }));
    await waitFor(() => expect(api.fetchProfile).toHaveBeenLastCalledWith("default", "web-copy"));

    fireEvent.click(screen.getByRole("button", { name: "Rename" }));
    fireEvent.change(dialog().getByLabelText("New name"), { target: { value: "www" } });
    fireEvent.click(dialog().getByRole("button", { name: "OK" }));
    expect(ren).toHaveBeenCalledWith("default", "web-copy", "www");
    await waitFor(() => expect(api.fetchProfile).toHaveBeenLastCalledWith("default", "www"));

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    fireEvent.click(dialog().getByRole("button", { name: "Delete" }));
    expect(del).toHaveBeenCalledWith("default", "www");
    expect(await screen.findByRole("heading", { name: "Profiles in default" })).toBeInTheDocument();
  });

  it("default profile can't be renamed or deleted; failures stay; dialogs cancel; viewers read-only", async () => {
    vi.spyOn(api, "fetchProfile").mockResolvedValue(prof({ name: "default", used_by: [] }));
    vi.spyOn(api, "renameProfile").mockRejectedValue(new Error("x"));
    const a = routes("/profiles/default/default");
    expect(await screen.findByText("Nothing.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Rename" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Delete" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    fireEvent.click(dialog().getByRole("button", { name: "Cancel" }));
    a.unmount();
    vi.spyOn(api, "fetchProfile").mockResolvedValue(prof());
    vi.spyOn(api, "deleteProfile").mockRejectedValue(new Error("in use"));
    vi.spyOn(api, "createProfile").mockRejectedValue(new Error("exists"));
    const b = routes("/profiles/default/web");
    await screen.findByRole("button", { name: "Delete" });
    for (const name of ["Rename", "Delete"]) {
      fireEvent.click(screen.getByRole("button", { name }));
      fireEvent.click(dialog().getByRole("button", { name: "Cancel" }));
    }
    fireEvent.click(screen.getByRole("button", { name: "Rename" }));
    fireEvent.click(dialog().getByRole("button", { name: "OK" }));
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    fireEvent.click(dialog().getByRole("button", { name: "OK" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    fireEvent.click(dialog().getByRole("button", { name: "Delete" }));
    expect(await screen.findByText("✗ Delete profile web: in use")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Profile web/ })).toBeInTheDocument();
    b.unmount();
    routes("/profiles/default/web", viewer());
    expect(await screen.findByLabelText("Description")).toBeDisabled();
    expect(screen.queryByRole("toolbar")).toBeNull();
  });

  it("shows load errors", async () => {
    vi.spyOn(api, "fetchProfile").mockRejectedValue(new Error("no such profile"));
    routes("/profiles/default/x");
    expect(await screen.findByRole("alert")).toHaveTextContent("no such profile");
  });
});

const img = (over: Partial<api.LocalImage> = {}): api.LocalImage => ({
  fingerprint: "abcdef0123456789",
  aliases: ["alpine", "alpine/edge"],
  description: "Alpine edge",
  type: "container",
  architecture: "x86_64",
  size: 3 * 1024 ** 2,
  created_at: "2026-01-02T00:00:00Z",
  uploaded_at: "",
  last_used_at: "",
  cached: false,
  public: false,
  auto_update: true,
  properties: { description: "Alpine edge", os: "Alpine" },
  project: "default",
  ...over,
});

describe("images", () => {
  it("lists images and manages aliases, edits and deletes", async () => {
    vi.spyOn(api, "fetchLocalImages").mockResolvedValue([
      img(),
      img({ fingerprint: "ffff00001111", aliases: [], description: "", cached: true, type: "virtual-machine", created_at: "0001-01-01", auto_update: false, properties: {} }),
    ]);
    const rm = vi.spyOn(api, "deleteImageAlias").mockResolvedValue(ok);
    const add = vi.spyOn(api, "addImageAlias").mockResolvedValue(ok);
    const upd = vi.spyOn(api, "updateImage").mockResolvedValue(ok);
    const del = vi.spyOn(api, "deleteImage").mockResolvedValue(ok);
    routes("/images");
    const row = (await screen.findByText("Alpine edge", { selector: "td" })).closest("tr")!;
    expect(row).toHaveTextContent("3.0 MiB");
    expect(screen.getByText("none (cached)")).toBeInTheDocument();
    expect(screen.getByText("VM")).toBeInTheDocument();
    fireEvent.click(within(row).getByRole("button", { name: "Remove alias alpine/edge" }));
    expect(rm).toHaveBeenCalledWith("default", "alpine/edge");
    fireEvent.click(within(row).getByRole("button", { name: "Add alias" }));
    fireEvent.change(dialog().getByLabelText("Alias"), { target: { value: "a3" } });
    fireEvent.click(dialog().getByRole("button", { name: "OK" }));
    expect(add).toHaveBeenCalledWith("default", "abcdef0123456789", "a3");
    fireEvent.click(within(row).getByRole("button", { name: "Edit" }));
    fireEvent.change(dialog().getByLabelText("Description"), { target: { value: "new desc" } });
    fireEvent.click(dialog().getByLabelText(/Auto-update/));
    fireEvent.click(dialog().getByLabelText(/Public/));
    fireEvent.click(dialog().getByRole("button", { name: "Save" }));
    expect(upd).toHaveBeenCalledWith("default", "abcdef0123456789", {
      properties: { description: "new desc", os: "Alpine" },
      public: true,
      auto_update: false,
    });
    const row2 = screen.getByText("none (cached)").closest("tr")!;
    fireEvent.click(within(row2).getByRole("button", { name: "Edit" }));
    expect((dialog().getByLabelText("Description") as HTMLInputElement).value).toBe("");
    fireEvent.click(dialog().getByRole("button", { name: "Cancel" }));
    fireEvent.click(within(row2).getByRole("button", { name: "Delete" }));
    expect(screen.getByRole("dialog", { name: "Delete image ffff00001111?" })).toBeInTheDocument();
    fireEvent.click(dialog().getByRole("button", { name: "Delete" }));
    expect(del).toHaveBeenCalledWith("default", "ffff00001111");
    for (const name of ["Add alias", "Delete"]) {
      fireEvent.click(within(row).getByRole("button", { name }));
      fireEvent.click(dialog().getByRole("button", { name: "Cancel" }));
    }
  });

  it("imports from a remote and uploads a file", async () => {
    vi.spyOn(api, "fetchLocalImages").mockResolvedValue([]);
    const imp = vi.spyOn(api, "importImage").mockResolvedValue(ok);
    const up = vi.spyOn(api, "uploadImage").mockResolvedValue(ok);
    routes("/images");
    expect(await screen.findByText("No images.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Import from remote" }));
    fireEvent.click(dialog().getByRole("button", { name: "Import" })); // no alias: ignored
    expect(imp).not.toHaveBeenCalled();
    fireEvent.change(dialog().getByLabelText("Remote"), { target: { value: "ubuntu" } });
    fireEvent.change(dialog().getByLabelText("Image alias on the remote"), { target: { value: " 24.04 " } });
    fireEvent.change(dialog().getByLabelText("Local alias (optional)"), { target: { value: "noble" } });
    fireEvent.click(dialog().getByLabelText("VM image"));
    fireEvent.click(dialog().getByLabelText("Keep it updated"));
    fireEvent.click(dialog().getByRole("button", { name: "Import" }));
    expect(imp).toHaveBeenCalledWith({ project: "default", remote: "ubuntu", alias: "24.04", local_alias: "noble", auto_update: false, vm: true });
    fireEvent.click(screen.getByRole("button", { name: "Import from remote" }));
    fireEvent.change(dialog().getByLabelText("Image alias on the remote"), { target: { value: "alpine/3.22" } });
    fireEvent.click(dialog().getByRole("button", { name: "Import" }));
    expect(imp).toHaveBeenLastCalledWith({ project: "default", remote: "images", alias: "alpine/3.22", auto_update: true, vm: false });
    fireEvent.click(screen.getByRole("button", { name: "Import from remote" }));
    fireEvent.click(dialog().getByRole("button", { name: "Cancel" }));

    const input = screen.getByLabelText("Image file") as HTMLInputElement;
    const click = vi.spyOn(input, "click");
    fireEvent.click(screen.getByRole("button", { name: "Upload image file…" }));
    expect(click).toHaveBeenCalled();
    const f = new File(["x"], "img.tar.gz");
    fireEvent.change(input, { target: { files: [f] } });
    fireEvent.change(dialog().getByLabelText("Alias (optional)"), { target: { value: "mine" } });
    fireEvent.click(dialog().getByRole("button", { name: "OK" }));
    expect(up).toHaveBeenCalledWith("default", f, "mine");
    fireEvent.change(input, { target: { files: [f] } });
    fireEvent.click(dialog().getByRole("button", { name: "OK" }));
    expect(up).toHaveBeenLastCalledWith("default", f, undefined);
    fireEvent.change(input, { target: { files: [] } });
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.change(input, { target: { files: [f] } });
    fireEvent.click(dialog().getByRole("button", { name: "Cancel" }));
  });

  it("viewers see a read-only list; errors and loading", async () => {
    vi.spyOn(api, "fetchLocalImages").mockResolvedValue([img()]);
    const a = routes("/images", viewer());
    await screen.findByText("alpine");
    expect(screen.queryByRole("toolbar")).toBeNull();
    expect(screen.queryByRole("button", { name: /Remove alias/ })).toBeNull();
    a.unmount();
    vi.spyOn(api, "fetchLocalImages").mockReturnValue(new Promise(() => {}));
    const b = routes("/images");
    expect(screen.getByRole("status")).toHaveTextContent("Loading");
    b.unmount();
    vi.spyOn(api, "fetchLocalImages").mockRejectedValue(new Error("nope"));
    routes("/images");
    expect(await screen.findByRole("alert")).toHaveTextContent("nope");
  });
});

const project = (over: Partial<api.Project> = {}): api.Project => ({
  name: "lab",
  description: "Lab",
  config: { "features.images": "true", "features.profiles": "true", "limits.instances": "5" },
  used_by: ["/1.0/profiles/default?project=lab"],
  ...over,
});

describe("projects", () => {
  it("lists projects and creates one with features", async () => {
    vi.spyOn(api, "fetchProjects").mockResolvedValue([project(), project({ name: "bare", config: {}, used_by: [] })]);
    vi.spyOn(api, "fetchProject").mockResolvedValue(project({ name: "new" }));
    const create = vi.spyOn(api, "createProject").mockResolvedValue(ok);
    routes("/projects");
    const row = (await screen.findByRole("link", { name: "lab" })).closest("tr")!;
    expect(row).toHaveTextContent("images, profiles");
    expect(row).toHaveTextContent("instances=5");
    expect(screen.getByRole("link", { name: "bare" }).closest("tr")).toHaveTextContent("–");
    fireEvent.click(screen.getByRole("button", { name: "Create project" }));
    fireEvent.click(dialog().getByRole("button", { name: "Create" })); // no name: ignored
    fireEvent.change(dialog().getByLabelText("Name"), { target: { value: " new " } });
    fireEvent.change(dialog().getByLabelText("Description"), { target: { value: "d" } });
    fireEvent.click(dialog().getByLabelText("Own networks"));
    fireEvent.click(dialog().getByRole("button", { name: "Create" }));
    expect(create).toHaveBeenCalledWith("new", {
      description: "d",
      config: {
        "features.images": "true",
        "features.profiles": "true",
        "features.storage.volumes": "true",
        "features.storage.buckets": "true",
        "features.networks": "true",
        "features.networks.zones": "false",
      },
    });
    expect(await screen.findByRole("heading", { name: "Project new" })).toBeInTheDocument();
  });

  it("list states", async () => {
    vi.spyOn(api, "fetchProjects").mockResolvedValue([]);
    const a = routes("/projects", viewer());
    await screen.findByRole("heading", { name: "Projects" });
    expect(screen.queryByRole("button", { name: "Create project" })).toBeNull();
    a.unmount();
    const b = routes("/projects");
    fireEvent.click(await screen.findByRole("button", { name: "Create project" }));
    fireEvent.click(dialog().getByRole("button", { name: "Cancel" }));
    b.unmount();
    vi.spyOn(api, "fetchProjects").mockRejectedValue(new Error("x"));
    routes("/projects");
    expect(await screen.findByRole("alert")).toHaveTextContent("x");
  });

  it("edits, renames and deletes a project", async () => {
    vi.spyOn(api, "fetchProject").mockResolvedValue(project());
    vi.spyOn(api, "fetchProjects").mockResolvedValue([]);
    const upd = vi.spyOn(api, "updateProject").mockResolvedValue(ok);
    const ren = vi.spyOn(api, "renameProject").mockResolvedValue(ok);
    const del = vi.spyOn(api, "deleteProject").mockResolvedValue(ok);
    routes("/projects/lab");
    expect(await screen.findByText(/Used by 1 objects/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "Lab 2" } });
    fireEvent.change(screen.getByLabelText(/Config/), { target: { value: "a: {b: 1}" } });
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/Config/), { target: { value: "limits.instances: 9" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "Save" })).toBeEnabled()); // slow under a full run
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(upd).toHaveBeenCalledWith("lab", { description: "Lab 2", config: { "limits.instances": "9" } });
    fireEvent.click(screen.getByRole("button", { name: "Rename" }));
    expect(dialog().getByText("Only empty projects can be renamed.")).toBeInTheDocument();
    fireEvent.change(dialog().getByLabelText("New name"), { target: { value: "lab2" } });
    fireEvent.click(dialog().getByRole("button", { name: "OK" }));
    expect(ren).toHaveBeenCalledWith("lab", "lab2");
    await waitFor(() => expect(api.fetchProject).toHaveBeenLastCalledWith("lab2"));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    fireEvent.click(dialog().getByRole("button", { name: "Delete" }));
    expect(del).toHaveBeenCalledWith("lab2");
    expect(await screen.findByRole("heading", { name: "Projects" })).toBeInTheDocument();
  });

  it("protects default; failures stay; dialogs cancel; viewers read-only; errors", async () => {
    vi.spyOn(api, "fetchProject").mockResolvedValue(project({ name: "default" }));
    const a = routes("/projects/default");
    expect(await screen.findByRole("button", { name: "Rename" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Delete" })).toBeDisabled();
    a.unmount();
    vi.spyOn(api, "fetchProject").mockResolvedValue(project());
    vi.spyOn(api, "renameProject").mockRejectedValue(new Error("not empty"));
    vi.spyOn(api, "deleteProject").mockRejectedValue(new Error("not empty"));
    const b = routes("/projects/lab");
    await screen.findByRole("button", { name: "Delete" });
    for (const name of ["Rename", "Delete"]) {
      fireEvent.click(screen.getByRole("button", { name }));
      fireEvent.click(dialog().getByRole("button", { name: "Cancel" }));
    }
    fireEvent.click(screen.getByRole("button", { name: "Rename" }));
    fireEvent.click(dialog().getByRole("button", { name: "OK" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    fireEvent.click(dialog().getByRole("button", { name: "Delete" }));
    expect(await screen.findByText("✗ Delete project lab: not empty")).toBeInTheDocument();
    b.unmount();
    const c = routes("/projects/lab", viewer());
    expect(await screen.findByLabelText("Description")).toBeDisabled();
    expect(screen.queryByRole("toolbar")).toBeNull();
    c.unmount();
    vi.spyOn(api, "fetchProject").mockRejectedValue(new Error("gone"));
    routes("/projects/zz");
    expect(await screen.findByRole("alert")).toHaveTextContent("gone");
  });
});

describe("3f API helpers", () => {
  it("send the right method, URL and body", async () => {
    api.setCsrf("t");
    const f = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ operation: null, metadata: null }))));
    const pbody = { description: "", config: {}, devices: {} };
    await api.fetchProfile("p", "w");
    await api.createProfile("p", "w", pbody);
    await api.updateProfile("p", "w", pbody);
    await api.renameProfile("p", "w", "x");
    await api.deleteProfile("p", "w");
    await api.updateImage("p", "abc", { properties: {}, public: false, auto_update: true });
    await api.deleteImage("p", "abc");
    await api.addImageAlias("p", "abc", "a");
    await api.deleteImageAlias("p", "alpine/edge x");
    await api.importImage({ project: "p", remote: "images", alias: "a", auto_update: true, vm: false });
    await api.fetchProject("lab");
    await api.createProject("lab", { description: "", config: {} });
    await api.updateProject("lab", { description: "", config: {} });
    await api.renameProject("lab", "l2");
    await api.deleteProject("lab");
    expect(f.mock.calls.map(([u, i]) => [u, (i as RequestInit).method, (i as RequestInit).body ?? null])).toEqual([
      ["/api/profiles/p/w", "GET", null],
      ["/api/profiles", "POST", JSON.stringify({ project: "p", name: "w", ...pbody })],
      ["/api/profiles/p/w", "PUT", JSON.stringify(pbody)],
      ["/api/profiles/p/w/rename", "POST", '{"name":"x"}'],
      ["/api/profiles/p/w", "DELETE", null],
      ["/api/images/p/abc", "PUT", '{"properties":{},"public":false,"auto_update":true}'],
      ["/api/images/p/abc", "DELETE", null],
      ["/api/images/p/abc/aliases", "POST", '{"name":"a"}'],
      ["/api/images/p/aliases/alpine/edge%20x", "DELETE", null],
      ["/api/images/import", "POST", '{"project":"p","remote":"images","alias":"a","auto_update":true,"vm":false}'],
      ["/api/projects/lab", "GET", null],
      ["/api/projects", "POST", '{"name":"lab","description":"","config":{}}'],
      ["/api/projects/lab", "PUT", '{"description":"","config":{}}'],
      ["/api/projects/lab/rename", "POST", '{"name":"l2"}'],
      ["/api/projects/lab", "DELETE", null],
    ]);
  });

  it("uploads images as raw bodies", async () => {
    api.setCsrf("t");
    const f = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify({ operation: null, metadata: null })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "LXD: exists" }), { status: 502 }))
      .mockResolvedValueOnce(new Response("x", { status: 500 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ operation: null, metadata: null })));
    const file = new File(["x"], "a b.tar.gz");
    await api.uploadImage("p", file, "mine");
    expect(f.mock.calls[0][0]).toBe("/api/images/upload?project=p&filename=a+b.tar.gz&alias=mine");
    expect(((f.mock.calls[0][1] as RequestInit).headers as Record<string, string>)["X-CSRF-Token"]).toBe("t");
    await expect(api.uploadImage("p", file)).rejects.toThrow("LXD: exists");
    await expect(api.uploadImage("p", file)).rejects.toThrow("HTTP 500");
    api.setCsrf(null);
    await api.uploadImage("p", file);
    expect(f.mock.calls[3][0]).toBe("/api/images/upload?project=p&filename=a+b.tar.gz");
    expect((f.mock.calls[3][1] as RequestInit).headers).toEqual({ "Content-Type": "application/octet-stream" });
  });
});


describe("image registries (LXD 6.10+)", () => {
  const regs: api.ImageRegistry[] = [
    { name: "images", description: "", protocol: "simplestreams", builtin: true, public: true, config: { url: "https://images.lxd.canonical.com" } },
    { name: "lab-mirror", description: "mcui test registry", protocol: "simplestreams", builtin: false, public: true, config: { url: "https://mirror.example.org" } },
  ];
  const server = (image_registries: boolean): api.ServerInfo => ({
    version: "6.10.1", clustered: true, server_name: "m1", auth: "trusted", auth_method: "tls", endpoint: "", features: { image_registries },
  });

  it("lists registries; admins add and delete custom ones", async () => {
    vi.spyOn(api, "fetchLocalImages").mockResolvedValue([]);
    vi.spyOn(api, "fetchServer").mockResolvedValue(server(true));
    vi.spyOn(api, "fetchImageRegistries").mockResolvedValue(regs);
    const add = vi.spyOn(api, "createImageRegistry").mockResolvedValue({ operation: null, metadata: null });
    const del = vi.spyOn(api, "deleteImageRegistry").mockResolvedValue({ operation: null, metadata: null });
    renderAt(<ImagesPage />);
    const section = await screen.findByRole("region", { name: "Image registries" });
    const builtin = (await within(section).findByText("images")).closest("tr")!;
    expect(builtin).toHaveTextContent("images (built-in)https://images.lxd.canonical.com");
    expect(within(builtin).queryByRole("button", { name: "Delete" })).toBeNull();
    fireEvent.click(within(section).getByRole("button", { name: "Add registry" }));
    const dlg = within(screen.getByRole("dialog", { name: "Add image registry" }));
    fireEvent.change(dlg.getByLabelText("Name"), { target: { value: "m2" } });
    fireEvent.change(dlg.getByLabelText("SimpleStreams URL"), { target: { value: "http://plain.example.org" } });
    expect(dlg.getByRole("alert")).toHaveTextContent("The URL must be https://");
    fireEvent.click(dlg.getByRole("button", { name: "Add" }));
    expect(add).not.toHaveBeenCalled();
    fireEvent.change(dlg.getByLabelText("SimpleStreams URL"), { target: { value: "https://m2.example.org" } });
    fireEvent.change(dlg.getByLabelText("Description"), { target: { value: "second" } });
    fireEvent.click(dlg.getByRole("button", { name: "Add" }));
    expect(add).toHaveBeenCalledWith({ name: "m2", url: "https://m2.example.org", description: "second" });
    const custom = within(section).getByText("lab-mirror").closest("tr")!;
    fireEvent.click(within(custom).getByRole("button", { name: "Delete" }));
    fireEvent.click(within(screen.getByRole("dialog", { name: "Delete image registry lab-mirror?" })).getByRole("button", { name: "Delete" }));
    expect(del).toHaveBeenCalledWith("lab-mirror");
  });

  it("hidden before LXD 6.10; read-only for viewers", async () => {
    vi.spyOn(api, "fetchLocalImages").mockResolvedValue([]);
    vi.spyOn(api, "fetchServer").mockResolvedValue(server(false));
    const list = vi.spyOn(api, "fetchImageRegistries").mockResolvedValue(regs);
    const { unmount } = renderAt(<ImagesPage />);
    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByRole("region", { name: "Image registries" })).toBeNull();
    expect(list).not.toHaveBeenCalled();
    unmount();
    vi.spyOn(api, "fetchServer").mockResolvedValue(server(true));
    renderAt(<ImagesPage />, { s: session({ user: { username: "v", role: "viewer" } }) });
    const section = await screen.findByRole("region", { name: "Image registries" });
    expect(await within(section).findByText("lab-mirror")).toBeInTheDocument();
    expect(within(section).queryByRole("button")).toBeNull();
  });
});
