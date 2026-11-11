import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router";
import { deleteAcl, fetchAcl, renameAcl, updateAcl, type AclBody, type AclRule } from "../../api";
import { useActions } from "../../actions";
import Confirm from "../../Confirm";
import { UsedBy } from "../../links";
import { useSession } from "../../session";
import { useApi } from "../../useApi";
import { NameDialog } from "../instance/Actions";
import { aclPath } from "./paths";
import { emptyRule } from "./request";
import RulesEditor from "./RulesEditor";

// LXD leaves empty fields out of a rule
const full = (rules: AclRule[] | null): AclRule[] => (rules ?? []).map((r) => ({ ...emptyRule(), ...r }));

export default function AclPage() {
  const { project = "default", name = "" } = useParams();
  const { user } = useSession();
  const { run, version } = useActions();
  const navigate = useNavigate();
  const acl = useApi(`acl:${project}/${name}:${version}`, () => fetchAcl(project, name));
  const [draft, setDraft] = useState<AclBody | null>(null);
  const [dialog, setDialog] = useState<"rename" | "delete" | null>(null);
  const admin = user.role === "admin";
  const a = acl.data;

  useEffect(() => {
    if (a) setDraft({ description: a.description, ingress: full(a.ingress), egress: full(a.egress), config: a.config ?? {} });
  }, [a]);

  return (
    <>
      <h1>
        ACL {name} <span className="muted">in {project}</span>
      </h1>
      {acl.error && <p role="alert">{acl.error}</p>}
      {a && draft && (
        <>
          {admin && (
            <div className="actions" role="toolbar" aria-label="ACL actions">
              <button type="button" className="primary" onClick={() => void run(`Save ACL ${name}`, () => updateAcl(project, name, draft))}>
                Save
              </button>
              <button type="button" onClick={() => setDialog("rename")}>
                Rename
              </button>
              <button type="button" className="danger" onClick={() => setDialog("delete")}>
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
          <RulesEditor label="Ingress" rules={draft.ingress} readOnly={!admin} onChange={(ingress) => setDraft({ ...draft, ingress })} />
          <RulesEditor label="Egress" rules={draft.egress} readOnly={!admin} onChange={(egress) => setDraft({ ...draft, egress })} />
          <p className="muted">
            Sources and destinations take addresses, CIDR ranges, ranges like <code>10.0.0.5-10.0.0.9</code>, other ACL names, or{" "}
            <code>@internal</code> / <code>@external</code> (OVN networks only: bridges refuse them); separate several with commas. ACLs in
            use can't be renamed.
          </p>
          <h2>Used by</h2>
          {(a.used_by ?? []).length === 0 ? (
            <p>Nothing.</p>
          ) : (
            <ul>
              {(a.used_by ?? []).map((u) => (
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
          title={`Rename ACL ${name}`}
          label="New name"
          initial={name}
          onCancel={() => setDialog(null)}
          onSubmit={(to) => {
            setDialog(null);
            void run(`Rename ACL ${name}`, () => renameAcl(project, name, to)).then((ok) => ok && navigate(aclPath(project, to)));
          }}
        />
      )}
      {dialog === "delete" && (
        <Confirm
          title={`Delete ACL ${name}?`}
          confirmLabel="Delete"
          danger
          onCancel={() => setDialog(null)}
          onConfirm={() => {
            setDialog(null);
            void run(`Delete ACL ${name}`, () => deleteAcl(project, name)).then((ok) => ok && navigate("/network-acls"));
          }}
        >
          <p>ACLs still attached to networks or NICs can't be deleted.</p>
        </Confirm>
      )}
    </>
  );
}
