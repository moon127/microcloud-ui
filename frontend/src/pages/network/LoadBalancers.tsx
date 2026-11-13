import { useState } from "react";
import {
  createLbPool,
  createLoadBalancer,
  deleteLbPool,
  deleteLoadBalancer,
  fetchLbPools,
  fetchLbPoolState,
  fetchLoadBalancers,
  updateLbPool,
  updateLoadBalancer,
  type LbBackend,
  type LbBody,
  type LbPool,
  type LbPort,
  type LoadBalancer,
  type PoolBody,
} from "../../api";
import { useActions } from "../../actions";
import Confirm from "../../Confirm";
import { useApi } from "../../useApi";

/** What an OVN network's load balancers can do here (LXD api_extensions, Phase 5b). */
export interface LbFeatures {
  pools: boolean;
  healthChecks: boolean;
}

const portText = (p: LbPort) =>
  `${p.protocol} ${p.listen_port} → ${p.target_pool ? `pool ${p.target_pool}` : (p.target_backend ?? []).join(", ") || "–"}`;
const backendText = (b: LbBackend) => `${b.name}: ${b.target_address}${b.target_port ? `:${b.target_port}` : ""}`;

/** An OVN network's load balancers (NW-4/7) and, on LXD 6.9+, their pools. */
export default function LoadBalancers({
  project,
  network,
  admin,
  features,
}: {
  project: string;
  network: string;
  admin: boolean;
  features: LbFeatures;
}) {
  const { run, version } = useActions();
  const { data, error } = useApi(`lbs:${project}/${network}:${version}`, () => fetchLoadBalancers(project, network));
  const pools = useApi(`lb-pools:${project}/${network}:${version}:${features.pools}`, () =>
    features.pools ? fetchLbPools(project, network) : Promise.resolve([]),
  );
  const [editing, setEditing] = useState<LoadBalancer | "new" | null>(null);
  const [deleting, setDeleting] = useState<LoadBalancer | null>(null);
  return (
    <section aria-label="Load balancers">
      <h2>Load balancers</h2>
      {error && <p className="muted">Load balancers unavailable: {error}</p>}
      {data && data.length === 0 && <p>None.</p>}
      {data && data.length > 0 && (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>Listen address</th>
                <th>Ports</th>
                <th>Backends</th>
                <th>Description</th>
                {admin && <th />}
              </tr>
            </thead>
            <tbody>
              {data.map((lb) => (
                <tr key={lb.listen_address}>
                  <td>{lb.listen_address}</td>
                  <td className="wrap">{lb.ports.map(portText).join("; ") || "–"}</td>
                  <td className="wrap">{lb.backends.map(backendText).join("; ") || "–"}</td>
                  <td>{lb.description}</td>
                  {admin && (
                    <td>
                      <button type="button" onClick={() => setEditing(lb)}>
                        Edit
                      </button>{" "}
                      <button type="button" className="danger" onClick={() => setDeleting(lb)}>
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
        <button type="button" onClick={() => setEditing("new")}>
          Add load balancer
        </button>
      )}
      {features.pools && (
        <Pools project={project} network={network} admin={admin} healthChecks={features.healthChecks} pools={pools.data ?? []} error={pools.error} />
      )}
      {editing && (
        <LbDialog
          initial={editing === "new" ? null : editing}
          pools={features.pools ? (pools.data ?? []).map((p) => p.name) : null}
          onCancel={() => setEditing(null)}
          onSave={(listen, body) => {
            setEditing(null);
            void run(editing === "new" ? `Add load balancer ${listen}` : `Save load balancer ${listen}`, () =>
              editing === "new"
                ? createLoadBalancer(project, network, { listen_address: listen, ...body })
                : updateLoadBalancer(project, network, listen, body),
            );
          }}
        />
      )}
      {deleting && (
        <Confirm
          title={`Delete load balancer ${deleting.listen_address}?`}
          confirmLabel="Delete"
          danger
          onCancel={() => setDeleting(null)}
          onConfirm={() => {
            const lb = deleting;
            setDeleting(null);
            void run(`Delete load balancer ${lb.listen_address}`, () => deleteLoadBalancer(project, network, lb.listen_address));
          }}
        />
      )}
    </section>
  );
}

const emptyPort = (pool: boolean): LbPort => ({
  protocol: "tcp",
  listen_port: "",
  ...(pool ? { target_pool: "" } : { target_backend: [] }),
  description: "",
});
const emptyBackend = (): LbBackend => ({ name: "", target_address: "", target_port: "", description: "" });

function LbDialog({
  initial,
  pools,
  onCancel,
  onSave,
}: {
  initial: LoadBalancer | null;
  /** pool names, or null when this LXD has no pools */
  pools: string[] | null;
  onCancel: () => void;
  onSave: (listen: string, body: LbBody) => void;
}) {
  const [listen, setListen] = useState(initial?.listen_address ?? "");
  const [allocate, setAllocate] = useState(false);
  const [description, setDescription] = useState(initial?.description ?? "");
  const [backends, setBackends] = useState<LbBackend[]>(() => (initial?.backends ?? []).map((b) => ({ ...emptyBackend(), ...b })));
  const [ports, setPorts] = useState<LbPort[]>(() => initial?.ports ?? []);
  const [tried, setTried] = useState(false);
  const setPort = (i: number, patch: Partial<LbPort>) => setPorts(ports.map((p, j) => (j === i ? { ...p, ...patch } : p)));
  const setBackend = (i: number, patch: Partial<LbBackend>) => setBackends(backends.map((b, j) => (j === i ? { ...b, ...patch } : b)));
  const names = backends.map((b) => b.name.trim()).filter(Boolean);
  const problem =
    !initial && !allocate && !listen.trim()
      ? "Enter the listen address, or allocate one"
      : backends.some((b) => !b.name.trim() || !b.target_address.trim())
        ? "Every backend needs a name and a target address"
        : ports.some((p) => !p.listen_port.trim() || (p.target_pool === undefined ? !(p.target_backend ?? []).length : !p.target_pool))
          ? "Every port needs listen ports and a pool or at least one backend"
          : null;
  return (
    <Confirm
      title={initial ? `Edit load balancer ${initial.listen_address}` : "Add load balancer"}
      confirmLabel="Save"
      onCancel={onCancel}
      onConfirm={() => {
        setTried(true);
        if (problem) return;
        onSave(allocate ? "0.0.0.0" : listen.trim(), {
          description: description.trim(),
          config: initial?.config ?? {},
          backends: backends.map((b) => ({ ...b, name: b.name.trim(), target_address: b.target_address.trim(), target_port: b.target_port.trim() })),
          ports: ports.map((p) => ({ ...p, listen_port: p.listen_port.trim() })),
        });
      }}
    >
      <label className="field">
        Listen address
        <input value={allocate ? "" : listen} readOnly={!!initial} disabled={allocate} onChange={(e) => setListen(e.target.value)} />
      </label>
      {!initial && (
        <label>
          <input type="checkbox" checked={allocate} onChange={(e) => setAllocate(e.target.checked)} /> Allocate an address from
          the uplink (IPv4)
        </label>
      )}
      <label className="field">
        Description
        <input value={description} onChange={(e) => setDescription(e.target.value)} />
      </label>
      <h3>Ports</h3>
      {ports.map((p, i) => (
        <fieldset key={i} className="inline" aria-label={`Port ${i + 1}`}>
          <label className="field">
            Protocol
            <select value={p.protocol} onChange={(e) => setPort(i, { protocol: e.target.value as LbPort["protocol"] })}>
              <option>tcp</option>
              <option>udp</option>
            </select>
          </label>
          <label className="field">
            Listen ports
            <input size={8} placeholder="80,443" value={p.listen_port} onChange={(e) => setPort(i, { listen_port: e.target.value })} />
          </label>
          {pools && (
            <label className="field">
              Send to
              <select
                value={p.target_pool === undefined ? "backends" : "pool"}
                onChange={(e) =>
                  setPort(i, e.target.value === "pool" ? { target_pool: "", target_backend: undefined } : { target_pool: undefined, target_backend: [] })
                }
              >
                <option value="pool">a pool</option>
                <option value="backends">backends</option>
              </select>
            </label>
          )}
          {p.target_pool !== undefined ? (
            <label className="field">
              Pool
              <select value={p.target_pool} onChange={(e) => setPort(i, { target_pool: e.target.value })}>
                <option value="">choose…</option>
                {(pools ?? []).map((n) => (
                  <option key={n}>{n}</option>
                ))}
              </select>
            </label>
          ) : (
            <label className="field">
              Backends
              <input
                size={14}
                placeholder={names.join(",") || "b1,b2"}
                value={(p.target_backend ?? []).join(",")}
                onChange={(e) => setPort(i, { target_backend: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })}
              />
            </label>
          )}
          <button type="button" aria-label={`Remove port ${i + 1}`} onClick={() => setPorts(ports.filter((_, j) => j !== i))}>
            ×
          </button>
        </fieldset>
      ))}
      <button type="button" onClick={() => setPorts([...ports, emptyPort(!!pools)])}>
        Add port
      </button>
      <h3>Backends</h3>
      {backends.map((b, i) => (
        <fieldset key={i} className="inline" aria-label={`Backend ${i + 1}`}>
          <label className="field">
            Name
            <input size={8} value={b.name} onChange={(e) => setBackend(i, { name: e.target.value })} />
          </label>
          <label className="field">
            Target address
            <input size={12} value={b.target_address} onChange={(e) => setBackend(i, { target_address: e.target.value })} />
          </label>
          <label className="field">
            Target ports
            <input size={8} placeholder="same" value={b.target_port} onChange={(e) => setBackend(i, { target_port: e.target.value })} />
          </label>
          <button type="button" aria-label={`Remove backend ${i + 1}`} onClick={() => setBackends(backends.filter((_, j) => j !== i))}>
            ×
          </button>
        </fieldset>
      ))}
      <button type="button" onClick={() => setBackends([...backends, emptyBackend()])}>
        Add backend
      </button>
      {tried && problem && <p role="alert">{problem}</p>}
    </Confirm>
  );
}

function Pools({
  project,
  network,
  admin,
  healthChecks,
  pools,
  error,
}: {
  project: string;
  network: string;
  admin: boolean;
  healthChecks: boolean;
  pools: LbPool[];
  error: string | null;
}) {
  const { run } = useActions();
  const [editing, setEditing] = useState<LbPool | "new" | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  return (
    <>
      <h3>Pools</h3>
      {error && <p className="muted">Pools unavailable: {error}</p>}
      {pools.length === 0 && !error && <p>No pools.</p>}
      {pools.length > 0 && (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>Pool</th>
                <th>Target</th>
                <th>Instances</th>
                {healthChecks && <th>Health check</th>}
                <th>Description</th>
                {admin && <th />}
              </tr>
            </thead>
            <tbody>
              {pools.map((p) => (
                <tr key={p.name}>
                  <td>{p.name}</td>
                  <td>
                    {p.config.protocol ?? "tcp"} {p.config.target_port ?? "–"}
                  </td>
                  <td className="wrap">
                    <PoolTargets project={project} network={network} pool={p} healthChecks={healthChecks} />
                  </td>
                  {healthChecks && (
                    <td>{p.config.healthcheck === "true" ? `every ${p.config["healthcheck.interval"] ?? "?"} s` : "off"}</td>
                  )}
                  <td>{p.description}</td>
                  {admin && (
                    <td>
                      <button type="button" onClick={() => setEditing(p)}>
                        Edit
                      </button>{" "}
                      <button type="button" className="danger" onClick={() => setDeleting(p.name)}>
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
        <button type="button" onClick={() => setEditing("new")}>
          Add pool
        </button>
      )}
      {editing && (
        <PoolDialog
          initial={editing === "new" ? null : editing}
          project={project}
          healthChecks={healthChecks}
          onCancel={() => setEditing(null)}
          onSave={(name, body) => {
            setEditing(null);
            void run(editing === "new" ? `Add pool ${name}` : `Save pool ${name}`, () =>
              editing === "new" ? createLbPool(project, network, name, body) : updateLbPool(project, network, name, body),
            );
          }}
        />
      )}
      {deleting && (
        <Confirm
          title={`Delete pool ${deleting}?`}
          confirmLabel="Delete"
          danger
          onCancel={() => setDeleting(null)}
          onConfirm={() => {
            const name = deleting;
            setDeleting(null);
            void run(`Delete pool ${name}`, () => deleteLbPool(project, network, name));
          }}
        >
          <p>LXD refuses while a load balancer port still sends to it.</p>
        </Confirm>
      )}
    </>
  );
}

/** A pool's instances, with each target's live status when health checks run. */
function PoolTargets({ project, network, pool, healthChecks }: { project: string; network: string; pool: LbPool; healthChecks: boolean }) {
  const live = healthChecks && pool.config.healthcheck === "true";
  const state = useApi(`lb-pool-state:${project}/${network}/${pool.name}:${live}`, () =>
    live ? fetchLbPoolState(project, network, pool.name) : Promise.resolve({ targets: [] }),
  );
  const status = new Map<string, string>();
  for (const t of state.data?.targets ?? []) {
    // a target per instance and listen port: show the worst
    const prev = status.get(t.name);
    if (prev !== "offline") status.set(t.name, t.status);
  }
  return (
    <>
      {pool.instances.length === 0
        ? "none"
        : pool.instances.map((i, n) => (
            <span key={i.name}>
              {n > 0 && ", "}
              {i.name}
              {status.has(i.name) && <span className={status.get(i.name) === "online" ? "muted" : undefined}> ({status.get(i.name)})</span>}
            </span>
          ))}
    </>
  );
}

function PoolDialog({
  initial,
  project,
  healthChecks,
  onCancel,
  onSave,
}: {
  initial: LbPool | null;
  project: string;
  healthChecks: boolean;
  onCancel: () => void;
  onSave: (name: string, body: PoolBody) => void;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [protocol, setProtocol] = useState<PoolBody["protocol"]>((initial?.config.protocol as PoolBody["protocol"]) ?? "tcp");
  const [targetPort, setTargetPort] = useState(initial?.config.target_port ?? "");
  const [instances, setInstances] = useState((initial?.instances ?? []).map((i) => i.name).join(", "));
  const [check, setCheck] = useState(initial?.config.healthcheck === "true");
  const [interval, setHcInterval] = useState(initial?.config["healthcheck.interval"] ?? "");
  const [tried, setTried] = useState(false);
  const problem = !name.trim() ? "Enter a name" : !targetPort.trim() ? "Enter the target port" : null;
  return (
    <Confirm
      title={initial ? `Edit pool ${initial.name}` : "Add pool"}
      confirmLabel="Save"
      onCancel={onCancel}
      onConfirm={() => {
        setTried(true);
        if (problem) return;
        onSave(name.trim(), {
          description: description.trim(),
          protocol,
          target_port: targetPort.trim(),
          instances: instances.split(",").map((s) => s.trim()).filter(Boolean),
          healthcheck: healthChecks && check,
          ...(healthChecks && check && Number(interval) > 0 ? { healthcheck_interval: Number(interval) } : {}),
        });
      }}
    >
      <label className="field">
        Name
        <input value={name} readOnly={!!initial} onChange={(e) => setName(e.target.value)} />
      </label>
      <label className="field">
        Protocol
        <select value={protocol} onChange={(e) => setProtocol(e.target.value as PoolBody["protocol"])}>
          <option>tcp</option>
          <option>udp</option>
        </select>
      </label>
      <label className="field">
        Target port
        <input size={8} value={targetPort} onChange={(e) => setTargetPort(e.target.value)} />
      </label>
      <label className="field">
        Instances in {project} (comma separated)
        <input value={instances} onChange={(e) => setInstances(e.target.value)} />
      </label>
      {healthChecks && (
        <>
          <label>
            <input type="checkbox" checked={check} onChange={(e) => setCheck(e.target.checked)} /> Health check the targets
          </label>
          {check && (
            <label className="field">
              Check every (seconds)
              <input size={4} placeholder="LXD's default" value={interval} onChange={(e) => setHcInterval(e.target.value)} />
            </label>
          )}
        </>
      )}
      <label className="field">
        Description
        <input value={description} onChange={(e) => setDescription(e.target.value)} />
      </label>
      {tried && problem && <p role="alert">{problem}</p>}
    </Confirm>
  );
}
