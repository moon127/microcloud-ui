import { useState } from "react";
import { useNavigate } from "react-router";
import {
  changeState,
  copyInstance,
  deleteInstance,
  fetchMembers,
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
import { useSession } from "../../session";
import { useApi } from "../../useApi";
import { stateActions } from "./state";

type Dialog = "delete" | "rename" | "copy" | "move" | "publish" | null;

const LABEL: Record<StateAction, string> = {
  start: "Start",
  stop: "Stop",
  restart: "Restart",
  freeze: "Freeze",
  unfreeze: "Unfreeze",
};

export default function Actions({ inst }: { inst: InstanceFull }) {
  const { user } = useSession();
  const { run } = useActions();
  const navigate = useNavigate();
  const [dialog, setDialog] = useState<Dialog>(null);
  if (user.role !== "admin") return null;
  const { project, name } = inst;
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
  const { run } = useActions();
  const members = useApi("members", fetchMembers);
  const pools = useApi("pools", fetchStoragePools);
  const [target, setTarget] = useState("");
  const [pool, setPool] = useState("");
  const [live, setLive] = useState(false);
  const vm = inst.type === "virtual-machine";
  return (
    <Confirm
      title={`Move ${inst.name}`}
      confirmLabel="Move"
      onCancel={onClose}
      onConfirm={() => {
        if (!target && !pool) return;
        onClose();
        void run(`Move ${inst.name}`, () =>
          moveInstance(inst.project, inst.name, { target: target || undefined, pool: pool || undefined, live }),
        );
      }}
    >
      {inst.status === "Running" && !live && (
        <p className="muted">Running instances are moved cold: stop it first{vm ? ", or use live migration" : ""}.</p>
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
      {vm && (
        <label>
          <input type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} /> Live migration
        </label>
      )}
    </Confirm>
  );
}
