// What can go wrong when evacuating or restoring a member, from the test cloud (LXD 6.10,
// docs/LIVE-TESTS.md C1, C2, C2a): LXD doesn't always refuse a second evacuation, and a
// VM live-migrated to a newer CPU can't live-migrate back.
import type { EvacuationMode, Instance, Member } from "../api";

const isVoter = (m: Member) => m.roles.some((r) => r.startsWith("database"));

/** The other members that are evacuated or not online. */
export const membersOut = (members: Member[], name: string) =>
  members.filter((m) => m.name !== name && m.status !== "Online");

/** Members still online, voting in the database, once ``name`` is out too. */
export const votersLeft = (members: Member[], name: string) =>
  members.filter((m) => m.name !== name && m.status === "Online" && isVoter(m));

/** Would this instance live-migrate when its member is evacuated in ``mode``? */
export function liveMigrates(i: Instance, mode: EvacuationMode | ""): boolean {
  if (i.type !== "virtual-machine" || i.status !== "Running" || !i.stateful) return false;
  if (mode) return mode === "live-migrate";
  return !i.evacuate || i.evacuate === "auto" || i.evacuate === "live-migrate";
}

/** Each member's CPU model, when the members don't all have the same one. */
export function mixedCpus(cpus: Record<string, string>): boolean {
  return new Set(Object.values(cpus).filter(Boolean)).size > 1;
}

/** VMs on ``name`` that an evacuation in ``mode`` would live-migrate (worth a warning on mixed CPUs). */
export const evacuationRisks = (instances: Instance[], name: string, mode: EvacuationMode | "") =>
  instances.filter((i) => i.location === name && liveMigrates(i, mode));

/** Running stateful VMs that a restore of ``name`` would live-migrate back from a member with another CPU. */
export const restoreRisks = (instances: Instance[], name: string, cpus: Record<string, string>) =>
  instances.filter(
    (i) => i.evacuated_from === name && i.location !== name && liveMigrates(i, "") && cpus[i.location] !== cpus[name],
  );
