import { useState } from "react";
import { useNavigate } from "react-router";
import {
  changeState,
  copyInstance,
  deleteInstance,
  fetchMembers,
  fetchProjects,
  fetchStoragePools,
  moveInstance,
  publishInstance,
  renameInstance,
  type InstanceFull,
  type StateAction,
} from "../../api";
import { useActions } from "../../actions";
import Confirm from "../../Confirm";
import { instancePath } from "../../paths";
import { useApi } from "../../useApi";
import { stateActions } from "./state";
import { useAccess } from "../../access";

type Dialog = "delete" | "rename" | "copy" | "move" | "publish" | null;

const LABEL: Record<StateAction, string> = {
  start: "Start",
  stop: "Stop",
  restart: "Restart",
  freeze: "Freeze",
  unfreeze: "Unfreeze",
};

export default function Actions({ inst }: { inst: InstanceFull }) {
  const access = useAccess();
  const { run } = useActions();
  const navigate = useNavigate();
  const [dialog, setDialog] = useState<Dialog>(null);
  const { project, name } = inst;
  if (!access.can(project, "operate")) return null;
  const manage = access.can(project, "manage"); // rename, copy, move, publish, delete
  const close = () => setDialog(null);

  return (
    <div className="actions" role="toolbar" aria-label="Instance actions">
      {stateActions(inst.status).map((a) => (
        <button key={a} type="button" onClick={() => void run(`${LABEL[a]} ${name}`, () => changeState(project, name, a))}>
          {LABEL[a]}
        </button>
      ))}
      {inst.status === "Running" && (
        <button type="button" onClick={() => void run(`Force stop ${name}`, () => changeState(project, name, "stop", true))}>
          Force stop
        </button>
      )}
      {manage && (
        <>
          <button type="button" onClick={() => setDialog("rename")}>
            Rename
          </button>
          <button type="button" onClick={() => setDialog("copy")}>
            Copy
          </button>
          <button type="button" onClick={() => setDialog("move")}>
            Move
          </button>
          <button type="button" onClick={() => setDialog("publish")}>
            Publish image
          </button>
          <button type="button" className="danger" onClick={() => setDialog("delete")}>
            Delete
          </button>
        </>
      )}

      {dialog === "delete" && (
        <Confirm
          title={`Delete ${name}?`}
          confirmLabel={inst.status === "Stopped" ? "Delete" : "Stop and delete"}
          danger
          onCancel={close}
          onConfirm={() => {
            close();
            void run(`Delete ${name}`, () => deleteInstance(project, name, inst.status !== "Stopped")).then(
              (ok) => ok && navigate("/instances"),
            );
          }}
        >
          <p>This removes the instance and all its snapshots. It can't be undone.</p>
          {inst.status !== "Stopped" && <p>It is {inst.status.toLowerCase()} and will be stopped first.</p>}
        </Confirm>
      )}
      {dialog === "rename" && (
        <NameDialog
          title={`Rename ${name}`}
          label="New name"
          note={inst.status !== "Stopped" ? "LXD only renames stopped instances." : undefined}
          onCancel={close}
          onSubmit={(to) => {
            close();
            void run(`Rename ${name} to ${to}`, () => renameInstance(project, name, to)).then(
              (ok) => ok && navigate(instancePath(project, to)),
            );
          }}
        />
      )}
      {dialog === "copy" && <CopyDialog inst={inst} onClose={close} />}
      {dialog === "move" && <MoveDialog inst={inst} onClose={close} />}
      {dialog === "publish" && (
        <NameDialog
          title={`Publish ${name} as an image`}
          label="Alias (optional)"
          optional
          note={inst.status === "Running" ? "Running instances must be stopped to publish (or publish a snapshot)." : undefined}
          onCancel={close}
          onSubmit={(alias) => {
            close();
            void run(`Publish ${name}`, () => publishInstance(project, name, alias ? { alias } : {}));
          }}
        />
      )}
    </div>
  );
}

export function NameDialog({
  title,
  label,
  note,
  optional,
  initial = "",
  onCancel,
  onSubmit,
}: {
  title: string;
  label: string;
  note?: string;
  optional?: boolean;
  initial?: string;
  onCancel: () => void;
  onSubmit: (value: string) => void;
}) {
  const [value, setValue] = useState(initial);
  const valid = optional ? true : value.trim() !== "";
  return (
    <Confirm title={title} confirmLabel="OK" onCancel={onCancel} onConfirm={() => valid && onSubmit(value.trim())}>
      {note && <p className="muted">{note}</p>}
      <label className="field">
        {label}
        <input value={value} onChange={(e) => setValue(e.target.value)} />
      </label>
    </Confirm>
  );
}

function CopyDialog({ inst, onClose }: { inst: InstanceFull; onClose: () => void }) {
  const { run } = useActions();
  const members = useApi("members", fetchMembers);
  const [to, setTo] = useState(`${inst.name}-copy`);
  const [target, setTarget] = useState("");
  const [start, setStart] = useState(false);
  return (
    <Confirm
      title={`Copy ${inst.name}`}
      confirmLabel="Copy"
      onCancel={onClose}
      onConfirm={() => {
        if (!to.trim()) return;
        onClose();
        void run(`Copy ${inst.name} to ${to}`, () =>
          copyInstance(inst.project, inst.name, { name: to.trim(), target: target || undefined, start }),
        );
      }}
    >
      <label className="field">
        New name
        <input value={to} onChange={(e) => setTo(e.target.value)} />
      </label>
      <label className="field">
        Member
        <select value={target} onChange={(e) => setTarget(e.target.value)}>
          <option value="">Let LXD choose</option>
          {(members.data ?? []).map((m) => (
            <option key={m.name} value={m.name}>
              {m.name}
            </option>
          ))}
        </select>
      </label>
      <label>
        <input type="checkbox" checked={start} onChange={(e) => setStart(e.target.checked)} /> Start the copy
      </label>
    </Confirm>
  );
}

function MoveDialog({ inst, onClose }: { inst: InstanceFull; onClose: () => void }) {
  const access = useAccess();
  const { run } = useActions();
  const navigate = useNavigate();
  const members = useApi("members", fetchMembers);
  const pools = useApi("pools", fetchStoragePools);
  const projects = useApi("projects", fetchProjects);
  const [target, setTarget] = useState("");
  const [pool, setPool] = useState("");
  const [project, setProject] = useState("");
  const [live, setLive] = useState(false);
  const [leaveSnapshots, setLeaveSnapshots] = useState(false);
  const vm = inst.type === "virtual-machine";
  // a project move needs manage in both (D83); LXD moves it stopped
  const others = (projects.data ?? []).filter((p) => p.name !== inst.project && access.can(p.name, "manage"));
  const running = inst.status !== "Stopped";
  const snaps = (inst.snapshots ?? []).length;
  return (
    <Confirm
      title={`Move ${inst.name}`}
      confirmLabel="Move"
      onCancel={onClose}
      onConfirm={() => {
        if (!target && !pool && !project) return;
        if (project && running) return; // LXD refuses it, with a message about pools
        onClose();
        const opts = {
          target: target || undefined,
          pool: pool || undefined,
          ...(project ? { project, instance_only: leaveSnapshots } : { live }),
        };
        void run(`Move ${inst.name}${project ? ` to ${project}` : ""}`, () => moveInstance(inst.project, inst.name, opts)).then(
          (ok) => ok && project && navigate(instancePath(project, inst.name)),
        );
      }}
    >
      {running && !live && !project && (
        <p className="muted">Running instances are moved cold: stop it first{vm ? ", or use live migration" : ""}.</p>
      )}
      {others.length > 0 && (
        <label className="field">
          To project
          <select value={project} onChange={(e) => setProject(e.target.value)}>
            <option value="">(stay in {inst.project})</option>
            {others.map((p) => (
              <option key={p.name} value={p.name}>
                {p.name}
                {p.config.restricted === "true" ? " (restricted)" : ""}
              </option>
            ))}
          </select>
        </label>
      )}
      {project && (
        <>
          {running && <p role="alert">Stop {inst.name} first: LXD moves an instance to another project stopped.</p>}
          <p className="muted">
            LXD applies {project}&apos;s limits and restrictions to it. Its volumes stay where they are, and its profiles
            and networks must exist in {project} too.
          </p>
          {snaps > 0 && (
            <label>
              <input type="checkbox" checked={leaveSnapshots} onChange={(e) => setLeaveSnapshots(e.target.checked)} /> Leave
              its {snaps} snapshot{snaps === 1 ? "" : "s"} behind (deleted with the original; a restricted project needs{" "}
              <code>restricted.snapshots=allow</code> to take them)
            </label>
          )}
        </>
      )}
      <label className="field">
        To member
        <select value={target} onChange={(e) => setTarget(e.target.value)}>
          <option value="">(stay on {inst.location})</option>
          {(members.data ?? [])
            .filter((m) => m.name !== inst.location)
            .map((m) => (
              <option key={m.name} value={m.name}>
                {m.name}
              </option>
            ))}
        </select>
      </label>
      <label className="field">
        To storage pool
        <select value={pool} onChange={(e) => setPool(e.target.value)}>
          <option value="">(keep current pool)</option>
          {(pools.data ?? []).map((p) => (
            <option key={p.name} value={p.name}>
              {p.name} ({p.driver})
            </option>
          ))}
        </select>
      </label>
      {vm && !project && (
        <label>
          <input type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} /> Live migration
        </label>
      )}
    </Confirm>
  );
}
