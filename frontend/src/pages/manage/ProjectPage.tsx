import { useState } from "react";
import { useNavigate, useParams } from "react-router";
import { deleteProject, fetchProject, renameProject, updateProject } from "../../api";
import { useActions } from "../../actions";
import Confirm from "../../Confirm";
import { useApi } from "../../useApi";
import YamlEditor from "../../YamlEditor";
import { NameDialog } from "../instance/Actions";
import { asConfig } from "../instance/state";
import { useAccess } from "../../access";

export default function ProjectPage() {
  const { name = "" } = useParams();
  const access = useAccess();
  const { run, version } = useActions();
  const navigate = useNavigate();
  const proj = useApi(`project:${name}:${version}`, () => fetchProject(name));
  const [description, setDescription] = useState("");
  const [config, setConfig] = useState<Record<string, string>>({});
  const [problem, setProblem] = useState<string | null>(null);
  const [dialog, setDialog] = useState<"rename" | "delete" | null>(null);
  const admin = access.clusterAdmin;
  const p = proj.data;
  const isDefault = name === "default";

  // take the loaded values into the form as they arrive, during render rather than in an
  // effect, so the form never shows a render's worth of stale values
  const [shown, setShown] = useState<unknown>(null);
  if (p !== shown) {
    setShown(p);
    if (p) {
      setDescription(p.description);
      setConfig(p.config);
    }
  }

  return (
    <>
      <h1>Project {name}</h1>
      {proj.error && <p role="alert">{proj.error}</p>}
      {p && (
        <>
          {admin && (
            <div className="actions" role="toolbar" aria-label="Project actions">
              <button
                type="button"
                className="primary"
                disabled={problem !== null}
                onClick={() => void run(`Save project ${name}`, () => updateProject(name, { description, config }))}
              >
                Save
              </button>
              <button type="button" disabled={isDefault} onClick={() => setDialog("rename")}>
                Rename
              </button>
              <button type="button" className="danger" disabled={isDefault} onClick={() => setDialog("delete")}>
                Delete
              </button>
            </div>
          )}
          <fieldset disabled={!admin}>
            <label className="field">
              Description
              <input value={description} onChange={(e) => setDescription(e.target.value)} />
            </label>
          </fieldset>
          <YamlEditor
            label="Config: features and limits (YAML)"
            value={config}
            readOnly={!admin}
            onChange={(v) => {
              const c = asConfig(v);
              setProblem(typeof c === "string" ? c : null);
              if (typeof c !== "string") setConfig(c);
            }}
          />
          {problem && <p role="alert">{problem}</p>}
          <p className="muted">Used by {p.used_by.length} objects (instances, profiles, images, volumes, networks).</p>
        </>
      )}
      {dialog === "rename" && (
        <NameDialog
          title={`Rename project ${name}`}
          label="New name"
          initial={name}
          note="Only empty projects can be renamed."
          onCancel={() => setDialog(null)}
          onSubmit={(to) => {
            setDialog(null);
            void run(`Rename project ${name}`, () => renameProject(name, to)).then(
              (ok) => ok && navigate(`/projects/${encodeURIComponent(to)}`),
            );
          }}
        />
      )}
      {dialog === "delete" && (
        <Confirm
          title={`Delete project ${name}?`}
          confirmLabel="Delete"
          danger
          onCancel={() => setDialog(null)}
          onConfirm={() => {
            setDialog(null);
            void run(`Delete project ${name}`, () => deleteProject(name)).then((ok) => ok && navigate("/projects"));
          }}
        >
          <p>Only empty projects can be deleted.</p>
        </Confirm>
      )}
    </>
  );
}
