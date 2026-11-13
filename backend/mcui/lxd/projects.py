"""LXD projects: whether one is restricted (D81)."""

from __future__ import annotations

from mcui.lxd.client import LXDClient, LXDError


class NoSuchProject(LookupError):
    pass


async def is_restricted(lxd: LXDClient, project: str) -> bool:
    """Is ``project`` restricted (``restricted=true``)? LXD then refuses the routes to the
    host (privileged containers, host-path disks, raw.lxc, nesting...) even to our admin
    certificate (checked on 6.10, 2026-11-13), which is what makes operator and admin
    grants safe. Raises ``NoSuchProject``."""
    try:
        data = await lxd.get(f"/1.0/projects/{project}")
    except LXDError as e:
        if e.status == 404:
            raise NoSuchProject(project) from e
        raise
    return (data.get("config") or {}).get("restricted") == "true"
