# Alerts

microcloud-ui checks the cluster every 30 seconds and raises an alert when something
needs attention: a Ceph OSD nearly full, a storage pool or host disk filling up, a member
offline, a network running out of addresses. Alerts show on every page and are recorded,
so you can see what happened while you weren't looking. (Phase 8a, D91.) They aren't sent
anywhere outside the app yet.

![The Alerts page, with a critical alert in the banner](screenshots/alerts.png)

## Seeing them

- **The bell** in the top bar counts the alerts nobody has acknowledged; red with any
  critical one, amber with warnings.
- **A toast** pops up, on whatever page you're on, when a new alert fires or a warning
  turns critical. Warnings go after 20 seconds; critical ones stay until you dismiss them,
  the alert clears or someone acknowledges it. A newly opened tab doesn't announce what
  was already active; reloading a tab doesn't repeat its toasts.
- **A banner** under the top bar stays while any critical alert is unacknowledged.
- **The Alerts page** (it replaces Warnings):
  - **Active**: what's wrong now, with a link to the page that shows it.
    **Acknowledge** hides an alert from everyone's bell and banner until it clears and
    comes back, or a warning turns critical; it stays on this list.
  - **History**: alerts that cleared, when, and how long they lasted (kept 90 days).
  - **LXD warnings**: LXD's own warnings, as before (cluster roles).

**Who sees what:** cluster admins and viewers see every alert. A project user sees only
their projects' alerts: their instances running out of memory or root disk, their
project nearing its limits, and the project's own networks (with `features.networks`)
running out of addresses. A cluster admin can acknowledge any alert, a project admin
their project's. Alerts follow a renamed project; a deleted project's alerts clear and
only cluster roles see them afterwards.

## What's checked

| Alert | When (defaults) | From |
|---|---|---|
| Ceph's health checks | whenever Ceph raises one: `OSD_NEARFULL`, `OSD_FULL`, `POOL_NEARFULL`, `OSD_DOWN`, `MON_DOWN`, `MON_DISK_LOW`, `PG_DEGRADED`, `SLOW_OPS`, `MON_CLOCK_SKEW`, `RECENT_CRASH` and any other; `HEALTH_ERR` ones are critical | the Ceph mgr |
| Ceph filling up | raw capacity used ≥ 75% (critical 85%), before Ceph's own warnings | the Ceph mgr |
| Local storage pools | ZFS, LVM, btrfs or dir pool on a member ≥ 80% (90%) | LXD |
| Host filesystems | any real filesystem on a member ≥ 85% (95%), counted as `df` does | node_exporter |
| Host memory | ≥ 90% (95%) in use | node_exporter |
| Host clocks | not synchronised (Ceph and OVN need it) | node_exporter |
| Members | offline (critical) or evacuated (warning) | LXD |
| LXD security | a member's LXD release misses a security fix | LXD |
| Instances out of memory | processes killed for lack of memory in the last 10 minutes | LXD metrics |
| Instance root disks | ≥ 90% (95%) | LXD metrics |
| Project limits | any `limits.*` ≥ 90% (98%) | LXD |
| OVN uplink | router addresses used from `ipv4.ovn.ranges` ≥ 80% (95%) | LXD |
| Network addresses | IPv4 addresses in use in a bridge or OVN network's subnet ≥ 80% (95%): instances' leases and static addresses, and forwards or load balancers that use the subnet | LXD |
| Certificates | the LXD cluster certificate, the app's client, metrics and HTTPS certificates expire within 30 days (7) | the files |
| Collection | collecting Ceph, host or LXD metrics keeps failing | the app |

Rules without node_exporter or Ceph simply find nothing.

**Fewer false alarms:** a threshold alert fires only once its condition has held for a
while (60 seconds, 2 minutes for memory and clocks), and clears only 2 points below its
threshold (2 days above, for certificates), so a value hovering at the line doesn't
flap. A warning turns critical at the critical threshold itself. If a rule can't look in
a round (the Ceph mgr unreachable, say), its alerts stay as they were rather than
clearing; if only one member can't be read (its node_exporter down, the member offline),
only that member's alerts stay as they were. A rule that fails unexpectedly is logged
once and treated the same way; the others carry on.

The checks run every 30 seconds (`interval_seconds`). LXD's own versions are asked every
30 minutes.

## Changing them

**Settings → Alerts** (cluster admins): switch each rule on or off, and change its warning
and critical thresholds (percent between 0 and 100; days above 0 for certificates).
Values saved there win over the config file: saving stores every threshold, so later
changes to `[alerts] thresholds` in the file take effect only after **Use the config
file**. Switching alerts off in the config file (`enabled = false`) clears every open
alert at the next start. The same settings in `[alerts]` of the config file:
[`CONFIGURATION.md`](CONFIGURATION.md#alerts-what-is-checked-and-when).

Every change and every acknowledgement is in the audit log.
