import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { renderAt, session } from "../../test/render";
import FilesTab from "./FilesTab";

afterEach(() => vi.restoreAllMocks());

const ok: api.Started = { operation: null, metadata: null };
const inst = (over: Partial<api.InstanceFull> = {}): api.InstanceFull => ({
  project: "p",
  name: "c1",
  type: "container",
  status: "Running",
  location: "m1",
  description: "",
  profiles: [],
  config: {},
  devices: {},
  ...over,
});

const dir = (path: string, entries: api.FileEntry[], extra: Partial<api.FileView> = {}): api.FileView => ({
  path,
  type: "directory",
  entries,
  truncated: false,
  ...extra,
});

function fakeFs(views: Record<string, api.FileView | Error>) {
  return vi.spyOn(api, "browseFiles").mockImplementation((_p, _n, path) => {
    const v = views[path];
    return v instanceof Error ? Promise.reject(v) : Promise.resolve(v);
  });
}

describe("FilesTab", () => {
  it("browses folders, symlinks and files", async () => {
    const browse = fakeFs({
      "/root": dir("/root", [
        { name: "notes", type: "directory", mode: "0755", uid: "0", gid: "0", modified: "today" },
        { name: "a.txt", type: "file", size: 2048, mode: "0644", uid: "0", gid: "0" },
        { name: "link", type: "symlink" },
      ]),
      "/root/notes": dir("/root/notes", []),
      "/root/link": { path: "/root/link", type: "symlink", target: "../etc", resolved: "/etc" },
      "/etc": { path: "/etc", type: "file", size: 3, mode: "0644" },
      "/": dir("/", [{ name: "root", type: "directory" }], { truncated: true }),
    });
    renderAt(<FilesTab inst={inst()} />);
    const a = await screen.findByRole("link", { name: "a.txt" });
    expect(a).toHaveAttribute("href", "/api/instances/p/c1/files/download?path=%2Froot%2Fa.txt");
    expect(a.closest("tr")).toHaveTextContent("2.0 KiB");
    expect(screen.getAllByText("0:0", { selector: "td" })).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "notes/" }));
    expect(await screen.findByText("Empty folder.")).toBeInTheDocument();
    expect(browse).toHaveBeenLastCalledWith("p", "c1", "/root/notes");
    fireEvent.click(screen.getByRole("button", { name: ".." }));
    fireEvent.click(await screen.findByRole("button", { name: "link" }));
    expect(await screen.findByText("../etc")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Follow" }));
    expect(await screen.findByRole("link", { name: "Download" })).toHaveAttribute(
      "href",
      "/api/instances/p/c1/files/download?path=%2Fetc",
    );
    // breadcrumb and the path box
    fireEvent.click(within(screen.getByRole("navigation", { name: "Breadcrumb" })).getByRole("button", { name: "/" }));
    expect(await screen.findByText("Only the first 500 entries show details.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: ".." })).toBeNull();
    fireEvent.change(screen.getByLabelText("Path"), { target: { value: "  " } });
    fireEvent.click(screen.getByRole("button", { name: "Go" }));
    fireEvent.change(screen.getByLabelText("Path"), { target: { value: "/root/notes" } });
    fireEvent.click(screen.getByRole("button", { name: "Go" }));
    await waitFor(() => expect(browse).toHaveBeenLastCalledWith("p", "c1", "/root/notes"));
    fireEvent.click(await screen.findByRole("button", { name: ".." }));
    await waitFor(() => expect(browse).toHaveBeenLastCalledWith("p", "c1", "/root"));
  });

  it("goes up from a top-level folder to /", async () => {
    const browse = fakeFs({ "/root": dir("/root", []), "/": dir("/", []) });
    renderAt(<FilesTab inst={inst()} />);
    fireEvent.click(await screen.findByRole("button", { name: ".." }));
    await waitFor(() => expect(browse).toHaveBeenLastCalledWith("p", "c1", "/"));
  });

  it("uploads, creates folders and deletes as admin", async () => {
    fakeFs({ "/root": dir("/root", [{ name: "old", type: "directory" }, { name: "f", type: "file", size: 1 }]) });
    const up = vi.spyOn(api, "uploadFile").mockResolvedValue(ok);
    const mk = vi.spyOn(api, "makeDirectory").mockResolvedValue(ok);
    const del = vi.spyOn(api, "deleteFile").mockResolvedValue(ok);
    renderAt(<FilesTab inst={inst()} />);
    await screen.findByRole("button", { name: "old/" });
    const input = screen.getByLabelText("Files to upload") as HTMLInputElement;
    const click = vi.spyOn(input, "click");
    fireEvent.click(screen.getByRole("button", { name: "Upload files…" }));
    expect(click).toHaveBeenCalled();
    const f1 = new File(["a"], "a.txt");
    const f2 = new File(["b"], "b.bin");
    fireEvent.change(input, { target: { files: [f1, f2] } });
    expect(up.mock.calls).toEqual([
      ["p", "c1", "/root/a.txt", f1],
      ["p", "c1", "/root/b.bin", f2],
    ]);
    fireEvent.change(input, { target: { files: null } });

    fireEvent.click(screen.getByRole("button", { name: "New folder" }));
    fireEvent.change(screen.getByLabelText("Folder name"), { target: { value: "new" } });
    fireEvent.click(screen.getByRole("button", { name: "OK" }));
    expect(mk).toHaveBeenCalledWith("p", "c1", "/root/new");
    fireEvent.click(screen.getByRole("button", { name: "New folder" }));
    fireEvent.change(screen.getByLabelText("Folder name"), { target: { value: "a/b" } });
    fireEvent.click(screen.getByRole("button", { name: "OK" }));
    expect(await screen.findByText(/✗ Create \/root\/a\/b: folder names can't contain/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "New folder" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    const rows = screen.getAllByRole("button", { name: "Delete" });
    fireEvent.click(rows[0]);
    expect(screen.getByRole("dialog", { name: "Delete /root/old?" })).toHaveTextContent("Folders must be empty");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(rows[1]);
    expect(screen.getByRole("dialog")).not.toHaveTextContent("Folders must be empty");
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }));
    expect(del).toHaveBeenCalledWith("p", "c1", "/root/f");
  });

  it("viewers browse read-only; errors and stopped VMs", async () => {
    fakeFs({ "/root": dir("/root", [{ name: "f", type: "file", size: 1 }], { entries: undefined }) });
    const a = renderAt(<FilesTab inst={inst()} />, { s: session({ user: { username: "v", role: "viewer" } }) });
    expect(await screen.findByText("Empty folder.")).toBeInTheDocument();
    expect(screen.queryByRole("toolbar")).toBeNull();
    a.unmount();
    fakeFs({ "/root": new Error("LXD: not found") });
    const b = renderAt(<FilesTab inst={inst()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("not found");
    b.unmount();
    vi.spyOn(api, "browseFiles").mockReturnValue(new Promise(() => {}));
    const c = renderAt(<FilesTab inst={inst()} />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading");
    c.unmount();
    renderAt(<FilesTab inst={inst({ type: "virtual-machine", status: "Stopped" })} />);
    expect(screen.getByText(/needs a running VM/)).toBeInTheDocument();
  });

  it("follows a symlink with no resolved path to /", async () => {
    const browse = fakeFs({ "/root": { path: "/root", type: "symlink", target: "x" }, "/": dir("/", []) });
    renderAt(<FilesTab inst={inst()} />);
    fireEvent.click(await screen.findByRole("button", { name: "Follow" }));
    await waitFor(() => expect(browse).toHaveBeenLastCalledWith("p", "c1", "/"));
  });
});

describe("file API helpers", () => {
  it("build URLs and upload raw bodies with CSRF", async () => {
    api.setCsrf("tok");
    const f = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ operation: null, metadata: null }))));
    await api.browseFiles("p", "c1", "/a b");
    await api.makeDirectory("p", "c1", "/d");
    await api.deleteFile("p", "c1", "/d");
    const blob = new Blob(["x"]);
    await api.uploadFile("p", "c1", "/up/x.bin", blob, "0600");
    expect(f.mock.calls.map(([u, i]) => [u, (i as RequestInit).method])).toEqual([
      ["/api/instances/p/c1/files?path=%2Fa%20b", "GET"],
      ["/api/instances/p/c1/files/mkdir?path=%2Fd", "POST"],
      ["/api/instances/p/c1/files?path=%2Fd", "DELETE"],
      ["/api/instances/p/c1/files/upload?path=%2Fup%2Fx.bin&mode=0600", "POST"],
    ]);
    const init = f.mock.calls[3][1] as RequestInit;
    expect(init.body).toBe(blob);
    expect((init.headers as Record<string, string>)["X-CSRF-Token"]).toBe("tok");
    expect(api.downloadUrl("p", "c1", "/x")).toBe("/api/instances/p/c1/files/download?path=%2Fx");

    f.mockResolvedValueOnce(new Response(JSON.stringify({ detail: "LXD: read-only" }), { status: 502 }));
    await expect(api.uploadFile("p", "c1", "/x", blob)).rejects.toThrow("LXD: read-only");
    f.mockResolvedValueOnce(new Response("<html>", { status: 500 }));
    await expect(api.uploadFile("p", "c1", "/x", blob)).rejects.toThrow("HTTP 500");
    api.setCsrf(null);
    f.mockResolvedValueOnce(new Response(JSON.stringify({ operation: null, metadata: null })));
    await api.uploadFile("p", "c1", "/x", blob);
    expect((f.mock.calls.at(-1)![1] as RequestInit).headers).toEqual({ "Content-Type": "application/octet-stream" });
    const last = f.mock.calls.at(-1)![0] as string;
    expect(last.endsWith("&mode=0644")).toBe(true);
  });
});
