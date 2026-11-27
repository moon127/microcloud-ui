import { useState } from "react";
import {
  addHostDevice,
  fetchHostInventory,
  fetchProject,
  patchInstanceConfig,
  removeHostDevice,
  type HostInventory,
  type HostUsb,
  type InstanceFull,
} from "../../api";
import { isHostDevice, useAccess } from "../../access";
import { useActions } from "../../actions";
import Confirm from "../../Confirm";
import { formatBytes } from "../../format";
import { MemberLink } from "../../links";
import { useApi } from "../../useApi";

const NAME = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;
const MOVES = new Set(["migrate", "live-migrate"]);

/** What a host device is, in a line. */
function describe(d: Record<string, string>): string {
  if (d.type === "usb") {
    const id = [d.vendorid, d.productid].filter(Boolean).join(":") || "any";
    return `USB ${id}${d.serial ? `, serial ${d.serial}` : ""}${d.busnum ? `, bus ${d.busnum} device ${d.devnum}` : ""}`;
  }
  if (d.type === "disk") {
    const to = d.path ? ` → ${d.path}` : " (block device)";
    return `${d.source}${to}${d.readonly === "true" ? ", read-only" : ""}${d.shift === "true" ? ", shifted" : ""}`;
  }
  return `${d.type}${d.source ? ` ${d.source}` : ""}`;
}

/** A device name from a label: lower case, dashes, starting with a letter or digit. */
const slug = (s: string, fallback: string) =>
  s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40) || fallback;

/** Host paths and USB devices of the member the instance runs on (D86). They reach the
 * host, so adding them needs the host-devices right; LXD's own auto evacuation then stops
 * the instance instead of moving it. */
export default function HostDevicesTab({ inst }: { inst: InstanceFull }) {
  const access = useAccess();
  const { run } = useActions();
  const { project, name } = inst;
  const may = access.hostDevices(project);
  const proj = useApi(`project:${project}`, () => fetchProject(project));
  // the member's hardware is only for those who may add it
  const inv = useApi(`host-inventory:${project}/${name}:${inst.location}:${may}`, () =>
    may ? fetchHostInventory(project, name) : Promise.resolve(null),
  );
  const [adding, setAdding] = useState<"path" | "usb" | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  const own = Object.entries(inst.devices ?? {}).filter(([, d]) => isHostDevice(d));
  const inherited = Object.entries(inst.expanded_devices ?? {}).filter(([n, d]) => isHostDevice(d) && !(n in (inst.devices ?? {})));
  const evacuate = inst.config["cluster.evacuate"] ?? "";
  const member = inst.location && inst.location !== "none" ? inst.location : "";
  const any = own.length + inherited.length > 0;
  const pc = proj.data?.config ?? {};
  const restricted = pc.restricted === "true";
  const vm = inst.type === "virtual-machine";

  return (
    <section aria-label="Host devices">
      <p className="muted">
        Folders, disks and USB devices of {member ? <MemberLink name={member} /> : "the host"} itself, for this instance.
        They tie it to members that have them: evacuating a member stops such an instance rather than moving it, unless
        every member has the same paths (an NFS mount, a SAN) and the instance is set to move.
        {vm && " A VM with a host path can't live-migrate."}
      </p>
      {any && member && (
        <p role="note">
          On evacuation of {member}:{" "}
          {MOVES.has(evacuate) ? (
            <>
              it moves (<code>cluster.evacuate={evacuate}</code>), so the same paths must exist on the member it lands on.
              USB devices stay behind.
            </>
          ) : evacuate === "stop" ? (
            <>
              it stops (<code>cluster.evacuate=stop</code>).
            </>
          ) : (
            <>
              LXD stops it (<code>cluster.evacuate</code> {evacuate ? `is ${evacuate}` : "is unset, i.e. auto"}).
            </>
          )}{" "}
          {access.can(project, "manage") && !MOVES.has(evacuate) && (
            <button
              type="button"
              onClick={() =>
                void run(`Move ${name} on evacuation`, () => patchInstanceConfig(project, name, { "cluster.evacuate": "migrate" }))
              }
            >
              Move it instead (the paths exist on every member)
            </button>
          )}
        </p>
      )}
      {!any ? (
        <p>None.</p>
      ) : (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Kind</th>
                <th>Details</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {own.map(([n, d]) => (
                <tr key={n}>
                  <td>{n}</td>
                  <td>{d.type === "disk" ? "Host path" : d.type === "usb" ? "USB" : d.type}</td>
                  <td>{describe(d)}</td>
                  <td className="actions">
                    {may && (
                      <button type="button" className="danger" onClick={() => setRemoving(n)}>
                        Remove
                      </button>
                    )}
                  </td>
                </tr>
              ))}
              {inherited.map(([n, d]) => (
                <tr key={n}>
                  <td>{n}</td>
                  <td>{d.type === "disk" ? "Host path" : d.type === "usb" ? "USB" : d.type}</td>
                  <td>{describe(d)}</td>
                  <td className="muted">from a profile</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {may ? (
        <p>
          <button type="button" onClick={() => setAdding("path")}>
            Add host path
          </button>{" "}
          <button type="button" onClick={() => setAdding("usb")}>
            Add USB device
          </button>
        </p>
      ) : (
        <p className="muted">
          Adding or removing them needs the host-devices right in {project}, which a cluster admin gives (Users → Projects).
        </p>
      )}
      {restricted && may && (
        <p className="muted">
          {project} is restricted:{" "}
          {pc["restricted.devices.disk"] === "allow"
            ? `host paths ${pc["restricted.devices.disk.paths"] ? `under ${pc["restricted.devices.disk.paths"]}` : "anywhere"} are allowed`
            : "LXD refuses host paths until Disk devices are allowed under its Restrictions"}
          ;{" "}
          {pc["restricted.devices.usb"] === "allow"
            ? "USB devices are allowed."
            : "LXD refuses USB devices until they're allowed there."}
        </p>
      )}
      {inv.error && <p className="muted">{member || "The host"}&apos;s devices unavailable: {inv.error}</p>}
      {adding === "path" && <AddPath inst={inst} inv={inv.data ?? undefined} onClose={() => setAdding(null)} />}
      {adding === "usb" && <AddUsb inst={inst} inv={inv.data ?? undefined} onClose={() => setAdding(null)} />}
      {removing && (
        <Confirm
          title={`Remove ${removing} from ${name}?`}
          confirmLabel="Remove"
          danger
          onCancel={() => setRemoving(null)}
          onConfirm={() => {
            const d = removing;
            setRemoving(null);
            void run(`Remove ${d} from ${name}`, () => removeHostDevice(project, name, d));
          }}
        >
          <p>
            {describe(inst.devices[removing])}. LXD takes it away at once, even while {name} runs; a program using it may
            fail.
          </p>
        </Confirm>
      )}
    </section>
  );
}

function AddPath({ inst, inv, onClose }: { inst: InstanceFull; inv?: HostInventory; onClose: () => void }) {
  const { run } = useActions();
  const vm = inst.type === "virtual-machine";
  const [source, setSource] = useState("");
  const [path, setPath] = useState("");
  const [name, setName] = useState("");
  const [readonly, setReadonly] = useState(true);
  const [shift, setShift] = useState(false);
  const [everywhere, setEverywhere] = useState(false);
  const [problem, setProblem] = useState("");
  const member = inv?.member || "the host";
  const disks = inv?.disks ?? [];
  const parts = disks.flatMap((d) =>
    d.partitions.length > 0
      ? d.partitions.map((p) => ({ label: `${d.model || d.id} ${p.id} (${formatBytes(p.size)})`, source: p.source }))
      : [{ label: `${d.model || d.id} (${formatBytes(d.size)})`, source: d.source }],
  );
  return (
    <Confirm
      title={`Add a host path to ${inst.name}`}
      confirmLabel="Add"
      onCancel={onClose}
      onConfirm={() => {
        const src = source.trim();
        const to = path.trim();
        const n = name.trim() || slug(src.split("/").filter(Boolean).pop() ?? "", "hostpath");
        if (!src.startsWith("/") || src.split("/").includes("..")) return setProblem("The host path is absolute, without ..");
        if (to && !to.startsWith("/")) return setProblem("The path inside is absolute.");
        if (!to && !vm) return setProblem("Choose where to mount it in the container.");
        if (!NAME.test(n)) return setProblem("Names start with a letter or digit: letters, digits, . _ -");
        onClose();
        void run(`Add ${src} to ${inst.name}`, () =>
          addHostDevice(inst.project, inst.name, {
            kind: "path",
            name: n,
            source: src,
            ...(to ? { path: to } : {}),
            readonly,
            ...(shift && !vm ? { shift } : {}),
            ...(everywhere ? { everywhere } : {}),
          }),
        );
      }}
    >
      <label className="field">
        Path on {member} <span className="muted">(a folder, a file, or a block device such as /dev/disk/by-id/…)</span>
        <input value={source} placeholder="/srv/nfs/media" onChange={(e) => setSource(e.target.value)} />
      </label>
      {parts.length > 0 && (
        <label className="field">
          Or a USB disk on {member}
          <select value="" onChange={(e) => setSource(e.target.value)}>
            <option value="">Choose…</option>
            {parts.map((p) => (
              <option key={p.source} value={p.source}>
                {p.label}
              </option>
            ))}
          </select>
        </label>
      )}
      <label className="field">
        Path inside the {vm ? "VM" : "container"}{" "}
        <span className="muted">{vm ? "(a folder is mounted there by the agent; leave empty for a block device)" : ""}</span>
        <input value={path} placeholder="/mnt/media" onChange={(e) => setPath(e.target.value)} />
      </label>
      <label className="field">
        Device name <span className="muted">(optional)</span>
        <input value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label>
        <input type="checkbox" checked={readonly} onChange={(e) => setReadonly(e.target.checked)} /> Read-only
      </label>
      {!vm && (
        <label>
          <input type="checkbox" checked={shift} onChange={(e) => setShift(e.target.checked)} /> Shift file ownership into the
          container (<code>shift</code>), so its users own their files
        </label>
      )}
      <label>
        <input type="checkbox" checked={everywhere} onChange={(e) => setEverywhere(e.target.checked)} /> Every member has this
        path (an NFS mount, a SAN): move the instance when its member is evacuated (<code>cluster.evacuate=migrate</code>)
      </label>
      <p className="muted">
        {everywhere
          ? "On evacuation LXD stops it, moves it and starts it again elsewhere, where the path must exist too."
          : `Evacuating ${member} will stop it rather than move it.`}
        {vm && " A VM with a host path can't live-migrate."} LXD adds it at once, even while the instance runs.
      </p>
      {problem && <p role="alert">{problem}</p>}
    </Confirm>
  );
}

const usbKey = (u: HostUsb) => `${u.busnum}-${u.devnum}`;

function AddUsb({ inst, inv, onClose }: { inst: InstanceFull; inv?: HostInventory; onClose: () => void }) {
  const { run } = useActions();
  const vm = inst.type === "virtual-machine";
  const devices = inv?.usb ?? [];
  const [chosen, setChosen] = useState("");
  const [name, setName] = useState("");
  const [problem, setProblem] = useState("");
  const member = inv?.member || "the host";
  const u = devices.find((d) => usbKey(d) === chosen);
  const mine = Object.values(inst.expanded_devices ?? inst.devices).filter((d) => d.type === "usb");
  const attached = (d: HostUsb) =>
    mine.some((m) => m.vendorid === d.vendor_id && m.productid === d.product_id && (!m.serial || m.serial === d.serial));
  // identical devices without a serial are told apart by where they're plugged in
  const twins = u ? devices.filter((d) => d.vendor_id === u.vendor_id && d.product_id === u.product_id).length > 1 : false;
  const byPort = Boolean(u && twins && !u.serial);
  return (
    <Confirm
      title={`Add a USB device to ${inst.name}`}
      confirmLabel="Add"
      onCancel={onClose}
      onConfirm={() => {
        if (!u) return setProblem("Choose a device.");
        const n = name.trim() || slug(u.product, "usb");
        if (!NAME.test(n)) return setProblem("Names start with a letter or digit: letters, digits, . _ -");
        onClose();
        void run(`Add USB ${u.product || `${u.vendor_id}:${u.product_id}`} to ${inst.name}`, () =>
          addHostDevice(inst.project, inst.name, {
            kind: "usb",
            name: n,
            vendorid: u.vendor_id,
            productid: u.product_id,
            ...(u.serial ? { serial: u.serial } : {}),
            ...(byPort && u.busnum !== null && u.devnum !== null ? { busnum: u.busnum, devnum: u.devnum } : {}),
          }),
        );
      }}
    >
      {!inv ? (
        <p>Reading {member}&apos;s devices…</p>
      ) : devices.length === 0 ? (
        <p>No USB devices on {member}.</p>
      ) : (
        <fieldset>
          <legend>USB devices on {member}</legend>
          {devices.map((d) => (
            <label key={usbKey(d)}>
              <input
                type="radio"
                name="usb"
                disabled={attached(d)}
                checked={chosen === usbKey(d)}
                onChange={() => setChosen(usbKey(d))}
              />{" "}
              {d.vendor} {d.product}{" "}
              <span className="muted">
                ({d.vendor_id}:{d.product_id}
                {d.serial ? `, serial ${d.serial}` : ""}, bus {d.busnum} device {d.devnum}
                {d.drivers.length > 0 ? `, ${d.drivers.join(", ")}` : ""})
              </span>
              {attached(d) && " (attached to this instance)"}
            </label>
          ))}
        </fieldset>
      )}
      <label className="field">
        Device name <span className="muted">(optional)</span>
        <input value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      {byPort && (
        <p className="muted">
          There are several of these without a serial number, so it&apos;s matched by where it&apos;s plugged in: after a
          replug or a reboot it may get another device number.
        </p>
      )}
      {vm ? (
        <p className="muted">The VM takes the whole device: it disappears from {member} while it&apos;s attached.</p>
      ) : (
        <p className="muted">
          A container sees only its <code>/dev/bus/usb</code> node, for programs that use libusb.
          {u?.storage && " To use this USB disk's files, add it as a host path instead (its disk is offered there)."}
        </p>
      )}
      <p className="muted">
        The device stays on {member}: evacuating it stops the instance, or with <code>cluster.evacuate=migrate</code> moves it
        without the device.
      </p>
      {problem && <p role="alert">{problem}</p>}
    </Confirm>
  );
}
