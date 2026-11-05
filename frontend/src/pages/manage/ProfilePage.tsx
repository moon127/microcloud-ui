import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { createProfile, deleteProfile, fetchProfile, renameProfile, updateProfile } from "../../api";
import { useActions } from "../../actions";
import Confirm from "../../Confirm";
import { instancePath } from "../../paths";
import { useSession } from "../../session";
import { useApi } from "../../useApi";
import YamlEditor from "../../YamlEditor";
import { NameDialog } from "../instance/Actions";
import { asConfig, asDevices } from "../instance/state";

interface Draft {
  description: string;
  config: Record<string, string>;
  devices: Record<string, Record<string, string>>;
}

/** "/1.0/instances/web?project=lab" -> link to that instance */
function UsedBy({ url }: { url: string }) {
  const u = new URL(url, "http://x");
  const m = u.pathname.match(/^\/1\.0\/instances\/([^/]+)$/);
  if (!m) return <code>{url}</code>;
  const project = u.searchParams.get("project") ?? "default";
  return <Link to={instancePath(project, decodeURIComponent(m[1]))}>{decodeURIComponent(m[1])}</Link>;
}

export default function ProfilePage() {
  const { project = "default", name = "" } = useParams();
  const { user } = useSession();
  const { run, version } = useActions();
  const navigate = useNavigate();
  const profile = useApi(`profile:${project}/${name}:${version}`, () => fetchProfile(project, name));
  const [draft, setDraft] = useState<Draft | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [dialog, setDialog] = useState<"rename" | "copy" | "delete" | null>(null);
  const admin = user.role === "admin";
  const p = profile.data;

  useEffect(() => {
    if (p) setDraft({ description: p.description, config: p.config, devices: p.devices });
  }, [p]);

  const base = `/profiles/${encodeURIComponent(project)}`;
  return (
    <>
      <h1>
        Profile {name} <span className="muted">in {project}</span>
      </h1>
      {profile.error && <p role="alert">{profile.error}</p>}
      {p && draft && (
        <>
          {admin && (
            <div className="actions" role="toolbar" aria-label="Profile actions">
              <button
                type="button"
                className="primary"
                disabled={problem !== null}
                onClick={() => void run(`Save profile ${name}`, () => updateProfile(project, name, draft))}
              >
                Save
              </button>
              <button type="button" onClick={() => setDialog("copy")}>
                Copy
              </button>
              <button type="button" onClick={() => setDialog("rename")} disabled={name === "default"}>
                Rename
              </button>
              <button type="button" className="danger" onClick={() => setDialog("delete")} disabled={name === "default"}>
                Delete
              </button>
            </div>
          )}
          <fieldset disabled={!admin}>
            <label className="field">
              Description
              <input value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} />
            </label>
          </fieldset>
          <div className="grid">
            <YamlEditor
              label="Config (YAML)"
              value={draft.config}
              readOnly={!admin}
              onChange={(v) => {
                const c = asConfig(v);
                setProblem(typeof c === "string" ? c : null);
                if (typeof c !== "string") setDraft((d) => d && { ...d, config: c });
              }}
            />
            <YamlEditor
              label="Devices (YAML)"
              value={draft.devices}
              readOnly={!admin}
              onChange={(v) => {
                const d = asDevices(v);
                setProblem(typeof d === "string" ? d : null);
                if (typeof d !== "string") setDraft((x) => x && { ...x, devices: d });
              }}
            />
          </div>
          {problem && <p role="alert">{problem}</p>}
          <h2>Used by</h2>
          {p.used_by.length === 0 ? (
            <p>Nothing.</p>
          ) : (
            <ul>
              {p.used_by.map((u) => (
                <li key={u}>
                  <UsedBy url={u} />
                </li>
              ))}
            </ul>
          )}
        </>
      )}
      {dialog === "rename" && (
        <NameDialog
          title={`Rename profile ${name}`}
          label="New name"
          initial={name}
          onCancel={() => setDialog(null)}
          onSubmit={(to) => {
            setDialog(null);
            void run(`Rename profile ${name}`, () => renameProfile(project, name, to)).then(
              (ok) => ok && navigate(`${base}/${encodeURIComponent(to)}`),
            );
          }}
        />
      )}
      {dialog === "copy" && draft && (
        <NameDialog
          title={`Copy profile ${name}`}
          label="New profile name"
          initial={`${name}-copy`}
          onCancel={() => setDialog(null)}
          onSubmit={(to) => {
            setDialog(null);
            void run(`Copy profile ${name} to ${to}`, () => createProfile(project, to, draft)).then(
              (ok) => ok && navigate(`${base}/${encodeURIComponent(to)}`),
            );
          }}
        />
      )}
      {dialog === "delete" && (
        <Confirm
          title={`Delete profile ${name}?`}
          confirmLabel="Delete"
          danger
          onCancel={() => setDialog(null)}
          onConfirm={() => {
            setDialog(null);
            void run(`Delete profile ${name}`, () => deleteProfile(project, name)).then((ok) => ok && navigate("/profiles"));
          }}
        >
          <p>Profiles still used by instances can't be deleted.</p>
        </Confirm>
      )}
    </>
  );
}
