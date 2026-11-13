import { useState } from "react";
import { Link } from "react-router";
import { createPeer, deletePeer, fetchNetworks, fetchPeers, fetchProjects, updatePeer, type NetworkPeer } from "../../api";
import { useAccess } from "../../access";
import { useActions } from "../../actions";
import Confirm from "../../Confirm";
import { useApi } from "../../useApi";
import { NameDialog } from "../instance/Actions";
import { networkPath } from "./paths";

const NAME = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;

/** An OVN network's peers (NW-7, Phase 10a): direct routes to other OVN networks on the
 * same uplink. A peer is Pending until the other network peers back. */
export default function Peers({ project, network, admin }: { project: string; network: string; admin: boolean }) {
  const { run, version } = useActions();
  const { data, error } = useApi(`peers:${project}/${network}:${version}`, () => fetchPeers(project, network));
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<NetworkPeer | null>(null);
  const [deleting, setDeleting] = useState<NetworkPeer | null>(null);
  return (
    <section aria-label="Peers">
      <h2>Peers</h2>
      {error && <p className="muted">Peers unavailable: {error}</p>}
      {data && data.length === 0 && <p>None.</p>}
      {data && data.length > 0 && (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Peered with</th>
                <th>Status</th>
                <th>Description</th>
                {admin && <th />}
              </tr>
            </thead>
            <tbody>
              {data.map((p) => (
                <tr key={p.name}>
                  <td>{p.name}</td>
                  <td>
                    <Link to={networkPath(p.target_project, p.target_network)}>
                      {p.target_project}/{p.target_network}
                    </Link>
                  </td>
                  <td>{p.status === "Pending" ? "Pending: the other network hasn't peered back" : p.status}</td>
                  <td>{p.description}</td>
                  {admin && (
                    <td className="actions">
                      <button type="button" onClick={() => setEditing(p)}>
                        Edit
                      </button>
                      <button type="button" className="danger" onClick={() => setDeleting(p)}>
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
      {admin && (
        <p>
          <button type="button" onClick={() => setAdding(true)}>
            Add peer
          </button>
        </p>
      )}
      {adding && <AddPeer project={project} network={network} onClose={() => setAdding(false)} />}
      {editing && (
        <NameDialog
          title={`Describe peer ${editing.name}`}
          label="Description"
          optional
          initial={editing.description}
          onCancel={() => setEditing(null)}
          onSubmit={(description) => {
            const p = editing;
            setEditing(null);
            void run(`Update peer ${p.name}`, () => updatePeer(project, network, p.name, { description, config: p.config }));
          }}
        />
      )}
      {deleting && (
        <Confirm
          title={`Delete peer ${deleting.name}?`}
          confirmLabel="Delete"
          danger
          onCancel={() => setDeleting(null)}
          onConfirm={() => {
            const p = deleting;
            setDeleting(null);
            void run(`Delete peer ${p.name}`, () => deletePeer(project, network, p.name));
          }}
        >
          <p>
            The networks stop routing to each other. The peer on {deleting.target_project}/{deleting.target_network}, if any,
            stays until it&apos;s deleted there too.
          </p>
        </Confirm>
      )}
    </section>
  );
}

function AddPeer({ project, network, onClose }: { project: string; network: string; onClose: () => void }) {
  const access = useAccess();
  const { run } = useActions();
  const projects = useApi("projects", fetchProjects);
  const [target, setTarget] = useState(project);
  const nets = useApi(`networks:${target}`, () => fetchNetworks(target));
  const choices = (nets.data ?? []).filter((n) => n.type === "ovn" && !(target === project && n.name === network));
  const [other, setOther] = useState("");
  const chosen = other || choices[0]?.name || "";
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const canBack = access.can(target, "manage");
  const [back, setBack] = useState(true);
  const [problem, setProblem] = useState("");
  return (
    <Confirm
      title={`Peer ${network} with another OVN network`}
      confirmLabel="Add"
      onCancel={onClose}
      onConfirm={() => {
        if (!chosen) return setProblem("Choose a network to peer with.");
        const n = name.trim() || `to-${chosen}`;
        if (!NAME.test(n)) return setProblem("Names start with a letter or digit: letters, digits, . _ -");
        onClose();
        void run(`Peer ${network} with ${target}/${chosen}`, async (progress) => {
          await createPeer(project, network, { name: n, description, target_project: target, target_network: chosen });
          if (!(back && canBack)) return;
          progress(`peering back from ${chosen}`);
          // the other side's peer: from there, to here
          return createPeer(target, chosen, { name: `to-${network}`, description, target_project: project, target_network: network });
        });
      }}
    >
      <p className="muted">
        Peered OVN networks on the same uplink route to each other directly. The peer is Pending until the other network
        peers back.
      </p>
      <label className="field">
        Project
        <select
          value={target}
          onChange={(e) => {
            setTarget(e.target.value);
            setOther("");
          }}
        >
          {(projects.data ?? [{ name: project }]).map((p) => (
            <option key={p.name}>{p.name}</option>
          ))}
        </select>
      </label>
      <label className="field">
        Network
        <select value={chosen} onChange={(e) => setOther(e.target.value)}>
          {choices.map((n) => (
            <option key={n.name}>{n.name}</option>
          ))}
        </select>
      </label>
      {nets.data && choices.length === 0 && <p className="muted">No other OVN network in {target}.</p>}
      <label className="field">
        Name (optional; {`to-${chosen || "…"}`} if empty)
        <input value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label className="field">
        Description
        <input value={description} onChange={(e) => setDescription(e.target.value)} />
      </label>
      {canBack ? (
        <label>
          <input type="checkbox" checked={back} onChange={(e) => setBack(e.target.checked)} /> Also peer back from{" "}
          {chosen || "the other network"} (as <code>to-{network}</code>), so the peering starts working
        </label>
      ) : (
        <p className="muted">Someone who can manage {target} has to peer back from there.</p>
      )}
      {problem && <p role="alert">{problem}</p>}
    </Confirm>
  );
}
