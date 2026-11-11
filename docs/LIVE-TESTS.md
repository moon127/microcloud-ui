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
