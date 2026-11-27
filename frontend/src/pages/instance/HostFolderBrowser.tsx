import { useState } from "react";
import { fetchHostFolder, type InstanceFull } from "../../api";
import { formatBytes } from "../../format";
import { useApi } from "../../useApi";

/** The folders above ``path``, for the breadcrumb: ["/", "/srv", "/srv/nfs"]. */
const crumbs = (path: string): string[] => {
  const parts = path.split("/").filter(Boolean);
  return ["/", ...parts.map((_, i) => "/" + parts.slice(0, i + 1).join("/"))];
};

const join = (dir: string, name: string) => (dir === "/" ? `/${name}` : `${dir}/${name}`);

/** Browse a folder on the instance's member through the app's helper (D87) and pick a
 * folder or file. The server leaves out and refuses what's hidden from the user. */
export default function HostFolderBrowser({
  inst,
  start,
  onPick,
}: {
  inst: InstanceFull;
  start: string;
  onPick: (path: string) => void;
}) {
  const [path, setPath] = useState(start);
  const folder = useApi(`host-folder:${inst.project}/${inst.name}:${path}`, () => fetchHostFolder(inst.project, inst.name, path));
  return (
    <div className="host-browser" role="group" aria-label="Folders on the member">
      <nav aria-label="Folder path">
        {crumbs(path).map((c, i) => (
          <span key={c}>
            {i > 1 && "/"}
            <button type="button" className="link" disabled={c === path} onClick={() => setPath(c)}>
              {c === "/" ? "/" : c.split("/").pop()}
            </button>
          </span>
        ))}
      </nav>
      {folder.error && <p role="alert">{folder.error}</p>}
      {folder.loading && !folder.data && <p role="status">Listing {path}… (the first time starts the helper)</p>}
      {folder.data && folder.data.path === path && (
        <>
          {folder.data.entries.length === 0 && <p className="muted">Empty, or nothing readable by ordinary users.</p>}
          <ul className="entries">
            {folder.data.entries.map((e) => (
              <li key={e.name}>
                {e.type === "dir" ? (
                  <button type="button" className="link" onClick={() => setPath(join(path, e.name))}>
                    📁 {e.name}/
                  </button>
                ) : e.type === "file" ? (
                  <button type="button" className="link" onClick={() => onPick(join(path, e.name))}>
                    {e.name}
                  </button>
                ) : (
                  <span className="muted">
                    {e.name} ({e.type === "link" ? "link" : "special file"})
                  </span>
                )}{" "}
                <span className="muted">
                  {e.mount ? `${e.mount} mount` : e.type === "file" ? formatBytes(e.size) : ""}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
      <p>
        <button type="button" onClick={() => onPick(path)}>
          Use {path}
        </button>
      </p>
    </div>
  );
}
