import type { NetworkType, NewNetwork } from "../../api";

/** The create-network form; empty fields are left to LXD's defaults. */
export interface NetworkForm {
  name: string;
  type: NetworkType;
  project: string;
  description: string;
  /** cluster-wide keys picked in the form (ipv4.address, bridge.mtu, …) */
  fields: Record<string, string>;
  /** member -> the member's own value (parent or bridge.external_interfaces) */
  perMember: Record<string, string>;
  /** anything else, from the YAML box */
  extra: Record<string, string>;
}

export interface Field {
  key: string;
  label: string;
  placeholder?: string;
  /** "" = LXD's default, "true", "false" */
  toggle?: boolean;
}

const ADDRESSES: Field[] = [
  { key: "ipv4.address", label: "IPv4 address (CIDR, auto or none)", placeholder: "auto" },
  { key: "ipv4.nat", label: "IPv4 NAT", toggle: true },
  { key: "ipv6.address", label: "IPv6 address (CIDR, auto or none)", placeholder: "auto" },
  { key: "ipv6.nat", label: "IPv6 NAT", toggle: true },
  { key: "bridge.mtu", label: "MTU", placeholder: "default" },
  { key: "dns.domain", label: "DNS domain", placeholder: "lxd" },
];

export const FIELDS: Record<NetworkType, Field[]> = {
  bridge: ADDRESSES,
  ovn: ADDRESSES,
  physical: [
    { key: "mtu", label: "MTU", placeholder: "parent's" },
    { key: "vlan", label: "VLAN id" },
    { key: "ipv4.gateway", label: "IPv4 gateway (CIDR, for an OVN uplink)", placeholder: "192.0.2.1/24" },
    { key: "ipv4.ovn.ranges", label: "IPv4 ranges for OVN routers", placeholder: "192.0.2.50-192.0.2.99" },
    { key: "ipv4.routes", label: "IPv4 routes OVN networks may use" },
    { key: "ipv6.gateway", label: "IPv6 gateway (CIDR)" },
    { key: "ipv6.ovn.ranges", label: "IPv6 ranges for OVN routers" },
    { key: "dns.nameservers", label: "DNS servers for OVN networks" },
  ],
  macvlan: [
    { key: "mtu", label: "MTU", placeholder: "parent's" },
    { key: "vlan", label: "VLAN id" },
  ],
};

/** The per-member key each type uses, and whether it's required. */
export const MEMBER_KEY: Record<NetworkType, { key: string; label: string; required: boolean } | null> = {
  bridge: { key: "bridge.external_interfaces", label: "External interfaces (optional)", required: false },
  ovn: null,
  physical: { key: "parent", label: "Parent interface", required: true },
  macvlan: { key: "parent", label: "Parent interface", required: true },
};

export const emptyNetwork = (project: string): NetworkForm => ({
  name: "",
  type: "bridge",
  project,
  description: "",
  fields: {},
  perMember: {},
  extra: {},
});

export function buildNetwork(f: NetworkForm, members: string[]): NewNetwork {
  const config: Record<string, string> = { ...f.extra };
  for (const { key } of FIELDS[f.type]) {
    const v = (f.fields[key] ?? "").trim();
    if (v) config[key] = v;
  }
  if (f.type === "ovn" && (f.fields.network ?? "").trim()) config.network = f.fields.network.trim();
  const mk = MEMBER_KEY[f.type];
  const perMember: NewNetwork["members"] = {};
  if (mk) {
    for (const m of members) {
      const v = (f.perMember[m] ?? "").trim();
      if (v) perMember[m] = { [mk.key]: v };
    }
  }
  return { name: f.name.trim(), type: f.type, project: f.project, description: f.description.trim(), config, members: perMember };
}

export function networkProblems(f: NetworkForm, members: string[]): string[] {
  const out: string[] = [];
  if (!/^[A-Za-z][A-Za-z0-9-]{0,14}$/.test(f.name.trim()))
    out.push("Name: up to 15 letters, digits and dashes, starting with a letter");
  const mk = MEMBER_KEY[f.type];
  if (mk?.required) {
    if (members.some((m) => !(f.perMember[m] ?? "").trim())) out.push(`${mk.label}: needed on every member`);
  }
  if (f.type === "ovn" && !(f.fields.network ?? "").trim()) out.push("Choose an uplink network");
  return out;
}
