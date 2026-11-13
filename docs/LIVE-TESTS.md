# Live tests for the owner

Everything below was built and fixture-tested but **not** run live by the Claude
session, either because it can't be confined to the `mcui-test` sandbox (D41) or
because it needs a real browser or a VM. Start the app as in the README
(`MCUI_CONFIG=~/.config/mcui/config.toml make serve`, then http://127.0.0.1:8000)
and sign in as an admin.

Tick each item and note anything odd; the audit log (`/api/audit`) records every
change with its LXD operation id.

## In a real browser (any instance)

| # | Steps | Expect |
|---|---|---|
| B1 | Instance → **Console** tab → Terminal | A shell prompt; typing works; resizing the window re-wraps (`stty size` changes); `exit` shows "[session ended]"; Reconnect opens a new shell |
| B2 | Same, on an instance on a *different* member from the one in `endpoints[0]` | Works the same (websockets go to the member running the instance) |
| B3 | **Console** tab → Text console on an **Ubuntu** container | A login prompt after pressing Enter. **Passed 2026-11-11** on the owner's Ubuntu containers and VMs. The Alpine and Debian sandbox containers send nothing to their console, even attached directly to LXD; that comes from the images, not from mcui |
| B4 | **Files** tab: browse `/etc`, follow a symlink, download a file, upload two files, create a folder, delete them | All work; uploads appear with mode 0644; deleting a non-empty folder shows LXD's refusal |
| B5 | **Images** → Upload image file… with a unified image tarball (`lxc image export <alias>` gives one) and an alias | Image appears with the alias once the import finishes |
| B6 | Overview / instance graphs in light and dark mode | Axes, legends and chart colours readable |

## Needs a VM (the sandbox allows none)

| # | Steps | Expect |
|---|---|---|
| V1 | Create a small VM (e.g. `images:alpine/3.22`, VM) in a project of your choice | Created and started |
| V2 | Console tab → **Graphical console** | The VM's screen; keyboard and mouse work; **Send Ctrl+Alt+Del** reboots/acts; **Full screen** works; Reconnect after closing works |
| V3 | Console tab → Text console on the VM | Serial console output/login. **Passed 2026-11-11** (Ubuntu VMs) |
| V4 | Files tab on the running VM | Works (via lxd-agent); on a stopped VM the tab explains it needs a running VM |
| V5 | Move the VM with **Live migration** ticked | Moves without stopping (needs `migration.stateful=true`) |

## Cluster-wide actions (can't be confined to the sandbox)

| # | Steps | Expect |
|---|---|---|
| C1 | Member page → **Evacuate** a member at a quiet time, mode *stop* or *migrate* | Toast follows the operation to the end; member shows *Evacuated*; instances moved/stopped as chosen |
| C2 | Same member → **Restore** | Member *Online*; instances back |
| C2b | Member page → **Evacuate** with another member already evacuated, then **Cancel**; on mixed CPUs with a running stateful VM on it, open **Evacuate** (Default mode), then switch to *migrate* | The dialog warns that another member is out, naming where instances would run and the voters left. It names the VMs that would live-migrate and the CPU models; the second warning disappears with *migrate*. During a real evacuation or restore, the progress line shows LXD's step and the instances on the member |
| C2a | LXD 6.9+: with one member evacuated, **Evacuate** a second one, then **Cancel** the force prompt | On three members LXD 6.10 does **not** refuse (see below). Don't run this on a cluster in use |

Done on LXD 6.10 (2026-11-13, member 2, Default mode): C1 moved four instances in about two
minutes, each down for a few seconds, and the member's OVN gateway moved off it. C2 failed
the first time: a VM with `migration.stateful=true`, live-migrated out without trouble,
failed to live-migrate back ("Failed restoring checkpoint"). LXD stopped it and left the
member evacuated. With that VM stopped, a second Restore moved everything back; a stopped
instance comes back stopped.

The cause was the members' CPUs. Members 1 and 2 have a Xeon D-1518 (Broadwell); member 3
has a Xeon D-2123IT (Skylake, with AVX-512). QEMU on member 2 refused the VM's state
("Failed to set XSAVE: Invalid argument" in its `qemu.log`), because a VM that has run on
the newer CPU can't resume on the older one. Live migration works only onto the newer one.

On a cluster with mixed CPUs, set `cluster.evacuate=migrate` on stateful VMs. They then
restart rather than live-migrate when a member is evacuated and restored.

C2b, the dialog part, passed on 2026-11-13: evacuating member 3 named its stateful VMs
and the CPU models, and the warning went away with *migrate (cold)*. The progress line
hasn't been seen during a real evacuation yet.

C2a on LXD 6.10 (2026-11-13, members 1 then 2):
- **LXD didn't refuse the second evacuation.** Evacuating member 1 demoted it from database
  voter, which left two voters. LXD's check (`checkEvacuationQuorum`) takes a majority of
  two as one, so it let member 2 go too. Every instance moved to member 3, which for a while
  was the only database voter. Raft needs two of two, so this looks like an LXD bug.
- **Restoring member 2 then failed twice** on the CPU difference above: its VMs had
  live-migrated to member 3's newer CPU. Each failure left member 2 evacuated, with no
  database role again.
- **What worked:** stop the VMs, restore member 2, then restore member 1. LXD runs only
  one evacuate or restore at a time ("An operation with this conflict reference is already
  running").
- **Autostart:** a stopped container with autostart set (`mirrors`) was started where
  the evacuation moved it.
| C3 | Warnings → Acknowledge / Reopen / Delete a harmless warning (e.g. the cgroup network priority one) | Status changes; deleted warnings come back if the condition persists |
| C4 | Projects → **Create** a throwaway project, edit its description/limits, **Rename** it, **Delete** it | Each step works; renaming/deleting a non-empty project shows LXD's refusal |
| C5 | Operations page while an image downloads | Running operation listed with Cancel; cancelling stops it |

## Networks (Phase 4; changes the real cluster, so yours to run)

| # | Steps | Expect |
|---|---|---|
| N1 | Networks → **Create network** → OVN, uplink `UPLINK`, name e.g. `test-ovn` | Created; its page shows the OVN gateway chassis and the router address in Allocated addresses; delete it afterwards |
| N2 | Create → Physical with a spare VLAN interface per member (if you have one) | Created on all members; per-member settings show each `parent` |
| N3 | Networks page and a network page in a real browser (light and dark) | Tables, per-member YAML boxes and the member selector are readable and work |
| N4 | On an OVN network: attach an ACL using `@internal`/`@external`, and add a forward from a spare `ipv4.ovn.ranges`/routes address to an instance | ACL applies; forward reaches the instance; remove both afterwards |

## Storage (Phase 4)

| # | Steps | Expect |
|---|---|---|
| S1 | Storage → a pool page and Custom volumes in a real browser | Capacity table/meter, history chart, per-member boxes (local pools only) readable |
| S2 | Create a volume in a project, attach it to a running container, check it's mounted, detach, delete | Each step works; delete refused while attached |
| S3 | On a VM: attach a **block** volume | Appears as a disk in the VM |

## Ceph (Phase 4)

| # | Steps | Expect |
|---|---|---|
| E1 | Ceph page in a real browser | Health line and checks, OSD/pool tables with meters, eight history charts, busiest disks with links |
| E2 | Stop the active mgr (`sudo microceph.ceph mgr fail`) and reload after ~30 s | Page keeps working from the new active mgr |

## OVN (Phase 4)

| # | Steps | Expect |
|---|---|---|
| O1 | Networks → OVN overview in a real browser | Uplink card with the router-address meter; OVN network table; links to networks, members and ACLs |
| O2 | After rebooting a member on MicroOVN 26.03: `sudo microovn.ovs-vsctl get open . other_config:flow-restore-wait` | Not `"true"`; if it is, clear it as in `docs/DEPLOY.md` (Supported releases). OVN overview shows an active gateway chassis per network, no error |

## Hosts (Phase 4g)

| # | Steps | Expect |
|---|---|---|
| H1 | Install node_exporter on some hosts as in `docs/HOST-METRICS.md`; run `mcui check` | `ok` for those hosts, `--` for the others; a wrong password shows `WARN ... HTTP 401` |
| H2 | Overview → Hosts in a real browser | Per host: CPU % (or load per CPU without node_exporter), memory (≈ without), swap, root disk and busiest disk, instances, allocated vCPU/memory with overcommit, network; meters turn amber at 60% and red at 85% |
| H3 | A member page | “Host (node_exporter)” graphs (CPU by mode, memory with cache/swap/ARC, filesystems, disk I/O and busy, pressure); without it, a note and LXD's memory graph |
| H4 | Load a host (`stress-ng --cpu 4 --timeout 60` in a sandbox container) | Host CPU rises within ~30 s and the CPU graph shows it as user time |

## Project users (Phase 6, D81)

Set `restricted=true` on a test project first (and restore it after); grant three project
users there: admin, operator, viewer.

| # | Steps | Expect |
|---|---|---|
| P1 | `mcui user grant <user> default admin` | Refused: `default` isn't restricted |
| P2 | As the project admin: create a container; then one with `security.privileged=true`, or a disk from `/` | The first is created; LXD refuses the others |
| P3 | As the operator: start, stop, then delete or create | Start and stop work; delete and create are refused (403) |
| P4 | As each user: open another project's instance, or try to start it | "Not found" (404) |
| P5 | As each user: the Instances, Operations and Overview pages, and the project picker | Only the granted project; no Storage, Ceph, Warnings or Users in the menu; Console and Files only for operator and admin |
| P6 | Open the event stream as the operator and start their instance | Only that project's events |

Done on the test cloud 2026-11-13 (P1–P6, 44 API checks and the UI as each user).

## Already verified live by the session (for reference)

Sandbox `mcui-test` (D41): instance create (local and remote images), start/stop/
restart/freeze/unfreeze, config update, snapshot create/rename/restore/delete,
copy, rename, move to another member, publish, delete and force delete; exec
terminal across members with resize; file list/upload/download/mkdir/delete
(3 MB byte-identical round trip); profile create/update/rename/delete; image
alias add/remove, edit, import from `images:`, delete; project description
update; `/api/events` relay; network create/edit/per-member edit/delete of a
`mcui-test-br` bridge on the real cluster (D41 extension); ACL create/edit/attach/
rename/delete and bridge forwards on it; volumes on Ceph RBD in `mcui-test`; pools and volumes
on zfs/btrfs/lvm/dir in the throwaway LXD; metrics history, graphs' data and Top-N.
