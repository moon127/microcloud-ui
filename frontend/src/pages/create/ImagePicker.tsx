import { useMemo, useState } from "react";
import { fetchLocalImages, fetchRemoteImages, type NewInstance, type RemoteImage } from "../../api";
import { formatBytes } from "../../format";
import { useApi } from "../../useApi";

type Source = "local" | "images" | "ubuntu";
type Choice = NewInstance["image"];

interface Row {
  key: string;
  label: string;
  detail: string;
  container: boolean;
  vm: boolean;
  choice: Choice;
}

const SOURCES: { id: Source; label: string }[] = [
  { id: "local", label: "Local images" },
  { id: "images", label: "images: (community)" },
  { id: "ubuntu", label: "ubuntu: (official)" },
];

interface Props {
  project: string;
  type: "container" | "virtual-machine";
  value: Choice | null;
  onChange: (c: Choice) => void;
}

export default function ImagePicker({ project, type, value, onChange }: Props) {
  const [source, setSource] = useState<Source>("images");
  const [search, setSearch] = useState("");
  const local = useApi(`images:${project}`, () => fetchLocalImages(project));
  // tag results with their source so a previous source's list is never shown
  const remote = useApi<{ source: Source; items: RemoteImage[] }>(`remote:${source}`, () =>
    source === "local"
      ? Promise.resolve({ source, items: [] as RemoteImage[] })
      : fetchRemoteImages(source).then((items) => ({ source, items })),
  );
  const remoteItems = remote.data?.source === source ? remote.data.items : null;

  const rows: Row[] = useMemo(() => {
    if (source === "local") {
      return (local.data ?? []).map((i) => ({
        key: i.fingerprint,
        label: i.aliases[0] ?? i.fingerprint.slice(0, 12),
        detail: `${i.description || i.properties.os || ""} · ${i.architecture} · ${formatBytes(i.size)}`,
        container: i.type === "container",
        vm: i.type === "virtual-machine",
        choice: i.aliases[0] ? { remote: "local", alias: i.aliases[0] } : { remote: "local", fingerprint: i.fingerprint },
      }));
    }
    return (remoteItems ?? []).map((i) => ({
      key: `${i.alias}:${i.arch}`,
      label: `${source}:${i.alias}`,
      detail: `${i.os} ${i.release_title} · ${i.variant} · ${i.arch}`,
      container: i.container,
      vm: i.vm,
      choice: { remote: source, alias: i.alias },
    }));
  }, [source, local.data, remoteItems]);

  const visible = rows.filter(
    (r) =>
      (type === "container" ? r.container : r.vm) &&
      (!search.trim() || `${r.label} ${r.detail}`.toLowerCase().includes(search.trim().toLowerCase())),
  );
  const error = source === "local" ? local.error : remote.error;
  const loading = source === "local" ? !local.data && !local.error : !remoteItems && !remote.error;
  const selected = (r: Row) => JSON.stringify(r.choice) === JSON.stringify(value);

  return (
    <div className="image-picker">
      <div className="tabs" role="group" aria-label="Image source">
        {SOURCES.map((s) => (
          <button key={s.id} type="button" aria-pressed={s.id === source} onClick={() => setSource(s.id)}>
            {s.label}
          </button>
        ))}
      </div>
      <input
        type="search"
        aria-label="Search images"
        placeholder="Search: alpine, noble, debian/12…"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />
      {error && <p role="alert">{error}</p>}
      {loading && <p role="status">Loading images…</p>}
      {!loading && !error && visible.length === 0 && (
        <p>No {type === "container" ? "container" : "VM"} images match.</p>
      )}
      {visible.length > 0 && (
        <div className="scroll image-list">
          <table>
            <tbody>
              {visible.slice(0, 200).map((r) => (
                <tr key={r.key} aria-selected={selected(r)} className={selected(r) ? "selected" : undefined}>
                  <td>
                    <button type="button" onClick={() => onChange(r.choice)} aria-label={`Use ${r.label}`}>
                      {selected(r) ? "Selected" : "Use"}
                    </button>
                  </td>
                  <td>{r.label}</td>
                  <td className="muted">{r.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
