import { useRef, useState } from "react";
import {
  addImageAlias,
  deleteImage,
  deleteImageAlias,
  fetchLocalImages,
  importImage,
  updateImage,
  uploadImage,
  type LocalImage,
} from "../../api";
import { useActions } from "../../actions";
import Confirm from "../../Confirm";
import { formatBytes } from "../../format";
import { useProject } from "../../project";
import { useSession } from "../../session";
import { useApi } from "../../useApi";
import { NameDialog } from "../instance/Actions";

type Dialog =
  | { kind: "alias" | "edit" | "delete"; image: LocalImage }
  | { kind: "import" }
  | { kind: "upload"; file: File }
  | null;

const date = (s: string) => (s && !s.startsWith("0001") ? new Date(s).toLocaleDateString() : "–");

export default function ImagesPage() {
  const { user } = useSession();
  const { run, version } = useActions();
  const project = useProject().project || "default";
  const { data, error } = useApi(`images:${project}:${version}`, () => fetchLocalImages(project));
  const [dialog, setDialog] = useState<Dialog>(null);
  const file = useRef<HTMLInputElement>(null);
  const admin = user.role === "admin";
  const close = () => setDialog(null);
  const short = (i: LocalImage) => i.aliases[0] ?? i.fingerprint.slice(0, 12);

  return (
    <>
      <h1>Images in {project}</h1>
      {admin && (
        <div className="actions" role="toolbar" aria-label="Image actions">
          <button type="button" className="primary" onClick={() => setDialog({ kind: "import" })}>
            Import from remote
          </button>
          <button type="button" onClick={() => file.current?.click()}>
            Upload image file…
          </button>
          <input
            ref={file}
            type="file"
            hidden
            aria-label="Image file"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) setDialog({ kind: "upload", file: f });
              e.target.value = "";
            }}
          />
        </div>
      )}
      {error && <p role="alert">{error}</p>}
      {!data && !error && <p role="status">Loading…</p>}
      {data && data.length === 0 && <p>No images.</p>}
      {data && data.length > 0 && (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>Aliases</th>
                <th>Description</th>
                <th>Type</th>
                <th>Arch</th>
                <th>Size</th>
                <th>Created</th>
                <th>Auto-update</th>
                <th>Fingerprint</th>
                {admin && <th />}
              </tr>
            </thead>
            <tbody>
              {data.map((i) => (
                <tr key={i.fingerprint}>
                  <td>
                    {i.aliases.length === 0 && <span className="muted">none{i.cached ? " (cached)" : ""}</span>}
                    {i.aliases.map((a) => (
                      <span key={a} className="chip">
                        {a}
                        {admin && (
                          <button
                            type="button"
                            aria-label={`Remove alias ${a}`}
                            onClick={() => void run(`Remove alias ${a}`, () => deleteImageAlias(project, a))}
                          >
                            ×
                          </button>
                        )}
                      </span>
                    ))}
                  </td>
                  <td>{i.description}</td>
                  <td>{i.type === "virtual-machine" ? "VM" : "container"}</td>
                  <td>{i.architecture}</td>
                  <td className="num">{formatBytes(i.size)}</td>
                  <td>{date(i.created_at)}</td>
                  <td>{i.auto_update ? "yes" : "no"}</td>
                  <td>
                    <code>{i.fingerprint.slice(0, 12)}</code>
                  </td>
                  {admin && (
                    <td className="actions">
                      <button type="button" onClick={() => setDialog({ kind: "alias", image: i })}>
                        Add alias
                      </button>
                      <button type="button" onClick={() => setDialog({ kind: "edit", image: i })}>
                        Edit
                      </button>
                      <button type="button" className="danger" onClick={() => setDialog({ kind: "delete", image: i })}>
                        Delete
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {dialog?.kind === "alias" && (
        <NameDialog
          title={`Add an alias to ${short(dialog.image)}`}
          label="Alias"
          onCancel={close}
          onSubmit={(alias) => {
            const fp = dialog.image.fingerprint;
            close();
            void run(`Add alias ${alias}`, () => addImageAlias(project, fp, alias));
          }}
        />
      )}
      {dialog?.kind === "edit" && <EditImage image={dialog.image} project={project} onClose={close} />}
      {dialog?.kind === "delete" && (
        <Confirm
          title={`Delete image ${short(dialog.image)}?`}
          confirmLabel="Delete"
          danger
          onCancel={close}
          onConfirm={() => {
            const i = dialog.image;
            close();
            void run(`Delete image ${short(i)}`, () => deleteImage(project, i.fingerprint));
          }}
        >
          <p>Instances already created from it keep working.</p>
        </Confirm>
      )}
      {dialog?.kind === "import" && <ImportImage project={project} onClose={close} />}
      {dialog?.kind === "upload" && (
        <NameDialog
          title={`Upload ${dialog.file.name}`}
          label="Alias (optional)"
          optional
          onCancel={close}
          onSubmit={(alias) => {
            const f = dialog.file;
            close();
            void run(`Upload image ${f.name}`, () => uploadImage(project, f, alias || undefined));
          }}
        />
      )}
    </>
  );
}

function EditImage({ image, project, onClose }: { image: LocalImage; project: string; onClose: () => void }) {
  const { run } = useActions();
  const [description, setDescription] = useState(image.properties.description ?? "");
  const [autoUpdate, setAutoUpdate] = useState(image.auto_update);
  const [pub, setPub] = useState(image.public);
  return (
    <Confirm
      title="Edit image"
      confirmLabel="Save"
      onCancel={onClose}
      onConfirm={() => {
        onClose();
        void run(`Save image ${image.fingerprint.slice(0, 12)}`, () =>
          updateImage(project, image.fingerprint, {
            properties: { ...image.properties, description },
            public: pub,
            auto_update: autoUpdate,
          }),
        );
      }}
    >
      <label className="field">
        Description
        <input value={description} onChange={(e) => setDescription(e.target.value)} />
      </label>
      <label>
        <input type="checkbox" checked={autoUpdate} onChange={(e) => setAutoUpdate(e.target.checked)} /> Auto-update from
        its source
      </label>
      <label>
        <input type="checkbox" checked={pub} onChange={(e) => setPub(e.target.checked)} /> Public (servable to other LXD
        servers)
      </label>
    </Confirm>
  );
}

function ImportImage({ project, onClose }: { project: string; onClose: () => void }) {
  const { run } = useActions();
  const [remote, setRemote] = useState<"images" | "ubuntu">("images");
  const [alias, setAlias] = useState("");
  const [local, setLocal] = useState("");
  const [vm, setVm] = useState(false);
  const [autoUpdate, setAutoUpdate] = useState(true);
  return (
    <Confirm
      title="Import an image"
      confirmLabel="Import"
      onCancel={onClose}
      onConfirm={() => {
        if (!alias.trim()) return;
        onClose();
        void run(`Import ${remote}:${alias.trim()}`, () =>
          importImage({
            project,
            remote,
            alias: alias.trim(),
            ...(local.trim() ? { local_alias: local.trim() } : {}),
            auto_update: autoUpdate,
            vm,
          }),
        );
      }}
    >
      <label className="field">
        Remote
        <select value={remote} onChange={(e) => setRemote(e.target.value as "images" | "ubuntu")}>
          <option value="images">images: (community)</option>
          <option value="ubuntu">ubuntu: (official)</option>
        </select>
      </label>
      <label className="field">
        Image alias on the remote
        <input placeholder="alpine/3.22, 24.04…" value={alias} onChange={(e) => setAlias(e.target.value)} />
      </label>
      <label className="field">
        Local alias (optional)
        <input value={local} onChange={(e) => setLocal(e.target.value)} />
      </label>
      <label>
        <input type="checkbox" checked={vm} onChange={(e) => setVm(e.target.checked)} /> VM image
      </label>
      <label>
        <input type="checkbox" checked={autoUpdate} onChange={(e) => setAutoUpdate(e.target.checked)} /> Keep it updated
      </label>
    </Confirm>
  );
}
