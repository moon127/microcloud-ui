import { useRef, useState } from "react";
import {
  browseFiles,
  deleteFile,
  downloadUrl,
  makeDirectory,
  uploadFile,
  type FileEntry,
  type InstanceFull,
} from "../../api";
import { useActions } from "../../actions";
import Confirm from "../../Confirm";
import { formatBytes } from "../../format";
import { useSession } from "../../session";
import { useApi } from "../../useApi";
import { NameDialog } from "./Actions";

const join = (dir: string, name: string) => (dir === "/" ? `/${name}` : `${dir}/${name}`);

function crumbs(path: string): { label: string; path: string }[] {
  const parts = path.split("/").filter(Boolean);
  return [{ label: "/", path: "/" }, ...parts.map((p, i) => ({ label: p, path: `/${parts.slice(0, i + 1).join("/")}` }))];
}

export default function FilesTab({ inst }: { inst: InstanceFull }) {
  const { user } = useSession();
  const { run, version } = useActions();
  const admin = user.role === "admin";
  const { project, name } = inst;
  const [path, setPath] = useState("/root");
  const [typed, setTyped] = useState(path);
  const [deleting, setDeleting] = useState<FileEntry | null>(null);
  const [mkdir, setMkdir] = useState(false);
  const upload = useRef<HTMLInputElement>(null);
  const view = useApi(`files:${project}/${name}:${path}:${version}`, () => browseFiles(project, name, path));

  const go = (p: string) => {
    setPath(p);
    setTyped(p);
  };

  if (inst.status !== "Running" && inst.type === "virtual-machine") {
    return <p className="muted">The file browser needs a running VM (it uses the LXD agent).</p>;
  }

  const v = view.data;
  return (
    <section aria-label="Files">
      <form
        className="form-row"
        onSubmit={(e) => {
          e.preventDefault();
          go(typed.trim() || "/");
        }}
      >
        <label className="field">
          Path
          <input value={typed} onChange={(e) => setTyped(e.target.value)} />
        </label>
        <button type="submit">Go</button>
      </form>
      <nav aria-label="Breadcrumb" className="actions">
        {crumbs(path).map((c) => (
          <button key={c.path} type="button" className="link" onClick={() => go(c.path)}>
            {c.label}
          </button>
        ))}
      </nav>
      {view.error && <p role="alert">{view.error}</p>}
      {!v && !view.error && <p role="status">Loading…</p>}

      {v?.type === "symlink" && (
        <p>
          Symbolic link to <code>{v.target}</code>.{" "}
          <button type="button" onClick={() => go(v.resolved ?? "/")}>
            Follow
          </button>
        </p>
      )}
      {v?.type === "file" && (
        <p>
          File, {formatBytes(v.size)}, mode {v.mode}.{" "}
          <a href={downloadUrl(project, name, v.path)} download>
            Download
          </a>
        </p>
      )}

      {v?.type === "directory" && (
        <>
          {admin && (
            <div className="actions" role="toolbar" aria-label="File actions">
              <button type="button" onClick={() => upload.current?.click()}>
                Upload files…
              </button>
              <input
                ref={upload}
                type="file"
                multiple
                hidden
                aria-label="Files to upload"
                onChange={(e) => {
                  for (const f of Array.from(e.target.files ?? [])) {
                    void run(`Upload ${f.name}`, () => uploadFile(project, name, join(path, f.name), f));
                  }
                  e.target.value = "";
                }}
              />
              <button type="button" onClick={() => setMkdir(true)}>
                New folder
              </button>
            </div>
          )}
          {v.truncated && <p className="muted">Only the first 500 entries show details.</p>}
          <div className="scroll">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Type</th>
                  <th className="num">Size</th>
                  <th>Mode</th>
                  <th>Owner</th>
                  <th>Modified</th>
                  {admin && <th />}
                </tr>
              </thead>
              <tbody>
                {path !== "/" && (
                  <tr>
                    <td>
                      <button type="button" className="link" onClick={() => go(path.split("/").slice(0, -1).join("/") || "/")}>
                        ..
                      </button>
                    </td>
                    <td colSpan={admin ? 6 : 5} />
                  </tr>
                )}
                {(v.entries ?? []).map((e) => (
                  <tr key={e.name}>
                    <td>
                      {e.type === "file" ? (
                        <a href={downloadUrl(project, name, join(path, e.name))} download>
                          {e.name}
                        </a>
                      ) : (
                        <button type="button" className="link" onClick={() => go(join(path, e.name))}>
                          {e.name}
                          {e.type === "directory" ? "/" : ""}
                        </button>
                      )}
                    </td>
                    <td>{e.type}</td>
                    <td className="num">{e.type === "file" ? formatBytes(e.size) : ""}</td>
                    <td>{e.mode}</td>
                    <td>{e.uid !== undefined ? `${e.uid}:${e.gid}` : ""}</td>
                    <td>{e.modified ?? ""}</td>
                    {admin && (
                      <td>
                        <button type="button" className="danger" onClick={() => setDeleting(e)}>
                          Delete
                        </button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
            {(v.entries ?? []).length === 0 && <p>Empty folder.</p>}
          </div>
        </>
      )}

      {deleting && (
        <Confirm
          title={`Delete ${join(path, deleting.name)}?`}
          confirmLabel="Delete"
          danger
          onCancel={() => setDeleting(null)}
          onConfirm={() => {
            const target = join(path, deleting.name);
            setDeleting(null);
            void run(`Delete ${target}`, () => deleteFile(project, name, target));
          }}
        >
          {deleting.type === "directory" && <p>Folders must be empty to delete.</p>}
        </Confirm>
      )}
      {mkdir && (
        <NameDialog
          title={`New folder in ${path}`}
          label="Folder name"
          onCancel={() => setMkdir(false)}
          onSubmit={(folder) => {
            setMkdir(false);
            void run(`Create ${join(path, folder)}`, () =>
              folder.includes("/")
                ? Promise.reject(new Error("folder names can't contain '/'"))
                : makeDirectory(project, name, join(path, folder)),
            );
          }}
        />
      )}
    </section>
  );
}
