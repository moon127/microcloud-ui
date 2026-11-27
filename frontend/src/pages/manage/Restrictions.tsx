/** A project's ``restricted.*`` keys as a form (PJ-2, Phase 7e). Empty means LXD's default,
 * which applies once ``restricted=true``. Allowing a "host" value lets the project's users
 * reach the host, which D81's operator and admin grants rely on LXD to prevent. */

interface Key {
  key: string;
  label: string;
  /** LXD's default when restricted, shown for the empty choice */
  default: string;
  options: string[];
  /** values that open a route to the host */
  host?: string[];
}

/** Device keys whose use the app also limits to the host-devices right (D86). */
const DEVICE_KEYS = new Set(["disk", "gpu", "usb", "pci", "unix-char", "unix-block"].map((d) => `restricted.devices.${d}`));
const PATHS = "restricted.devices.disk.paths";

const KEYS: Key[] = [
  { key: "restricted.snapshots", label: "Snapshots", default: "block", options: ["block", "allow"] },
  { key: "restricted.backups", label: "Backups", default: "block", options: ["block", "allow"] },
  { key: "restricted.cluster.target", label: "Choosing a member", default: "block", options: ["block", "allow"] },
  {
    key: "restricted.containers.privilege",
    label: "Container privilege",
    default: "unprivileged",
    options: ["unprivileged", "isolated", "allow"],
    host: ["allow"],
  },
  { key: "restricted.containers.nesting", label: "Container nesting", default: "block", options: ["block", "allow"], host: ["allow"] },
  {
    key: "restricted.containers.lowlevel",
    label: "Container low-level options (raw.lxc, volatile.*, …)",
    default: "block",
    options: ["block", "allow"],
    host: ["allow"],
  },
  {
    key: "restricted.virtual-machines.lowlevel",
    label: "VM low-level options (raw.qemu, …)",
    default: "block",
    options: ["block", "allow"],
    host: ["allow"],
  },
  { key: "restricted.devices.disk", label: "Disk devices", default: "managed", options: ["block", "managed", "allow"], host: ["allow"] },
  { key: "restricted.devices.nic", label: "NIC devices", default: "managed", options: ["block", "managed", "allow"] },
  { key: "restricted.devices.gpu", label: "GPU devices", default: "block", options: ["block", "allow"], host: ["allow"] },
  { key: "restricted.devices.usb", label: "USB devices", default: "block", options: ["block", "allow"], host: ["allow"] },
  { key: "restricted.devices.pci", label: "PCI devices", default: "block", options: ["block", "allow"], host: ["allow"] },
  { key: "restricted.devices.unix-char", label: "Unix character devices", default: "block", options: ["block", "allow"], host: ["allow"] },
  { key: "restricted.devices.unix-block", label: "Unix block devices", default: "block", options: ["block", "allow"], host: ["allow"] },
  { key: "restricted.devices.proxy", label: "Proxy devices", default: "block", options: ["block", "allow"] },
];

/** The keys set to a value that reaches the host. */
const hostRoutes = (config: Record<string, string>) => KEYS.filter((k) => k.host?.includes(config[k.key] ?? ""));

export default function Restrictions({
  config,
  onChange,
  disabled,
}: {
  config: Record<string, string>;
  onChange: (config: Record<string, string>) => void;
  disabled: boolean;
}) {
  const restricted = config.restricted === "true";
  const set = (key: string, value: string) => {
    const next = { ...config };
    if (value) next[key] = value;
    else delete next[key];
    onChange(next);
  };
  const risky = hostRoutes(config);
  const devices = risky.filter((k) => DEVICE_KEYS.has(k.key)).map((k) => k.label);
  const others = risky.filter((k) => !DEVICE_KEYS.has(k.key)).map((k) => k.label);
  const anyDisk = config["restricted.devices.disk"] === "allow";
  return (
    <section aria-label="Restrictions">
      <h2>Restrictions</h2>
      <fieldset disabled={disabled}>
        <label>
          <input type="checkbox" checked={restricted} onChange={(e) => set("restricted", e.target.checked ? "true" : "")} />{" "}
          Restricted: LXD keeps this project&apos;s instances away from the host (needed for operator and admin grants)
        </label>
        {restricted && (
          <div className="form-row">
            {KEYS.map((k) => (
              <label className="field" key={k.key}>
                {k.label}
                <select value={config[k.key] ?? ""} onChange={(e) => set(k.key, e.target.value)}>
                  <option value="">default ({k.default})</option>
                  {k.options.map((o) => (
                    <option key={o} value={o}>
                      {o}
                      {k.host?.includes(o) ? " (reaches the host)" : ""}
                    </option>
                  ))}
                </select>
              </label>
            ))}
          </div>
        )}
        {restricted && anyDisk && (
          <label className="field">
            Allowed host paths for disks <span className="muted">({PATHS}; comma-separated prefixes, empty = any)</span>
            <input value={config[PATHS] ?? ""} placeholder="/srv/nfs,/mnt/san" onChange={(e) => set(PATHS, e.target.value)} />
          </label>
        )}
        {restricted && anyDisk && !config[PATHS] && (
          <p role="alert">
            Any host path can be mounted: set allowed paths, or a writable shifted mount of a program folder gives root on the
            host.
          </p>
        )}
        {restricted && others.length > 0 && (
          <p role="alert">
            Allowed here: {others.join(", ")}. These let the project&apos;s users reach the host, so its operators and admins
            effectively have the host too (<code>docs/USERS.md</code>).
          </p>
        )}
        {restricted && devices.length > 0 && (
          <p className="muted">
            Allowed here: {devices.join(", ")}. The app lets only cluster admins, and project admins given the host-devices
            right (Users → Projects), add these to instances or profiles.
          </p>
        )}
      </fieldset>
      <p className="muted">
        Other <code>restricted.*</code> keys (networks, paths, idmaps) are in the YAML below.
      </p>
    </section>
  );
}
