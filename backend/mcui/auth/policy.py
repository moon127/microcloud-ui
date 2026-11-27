"""Who may do what in which LXD project (D81, Phase 6).

Global roles: ``admin`` may do everything, ``viewer`` may read everything. A ``project``
user has no global role, only grants: ``viewer`` reads a project, ``operator`` also runs
its instances (lifecycle, consoles, files, snapshots), ``admin`` also creates, changes and
deletes in it. The app talks to LXD as admin, so these checks are the boundary; LXD's
``restricted=true`` on the project still closes the routes to the host (verified
2026-11-13), which is why operate and manage need a restricted project.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from mcui.auth.store import User

Action = Literal["read", "operate", "manage"]
_NEED: dict[Action, int] = {"read": 0, "operate": 1, "manage": 2}
_RANK = {"viewer": 0, "operator": 1, "admin": 2}


def allowed(user: User, project: str, action: Action) -> bool:
    """May ``user`` do ``action`` in ``project``, as far as roles and grants go? (The
    restricted-project check for project users is separate: it needs LXD.)"""
    if user.role == "admin":
        return True
    if user.role == "viewer":
        return action == "read"
    role = user.grants.get(project)
    return role is not None and _RANK[role] >= _NEED[action]


def may_use_host_devices(user: User, project: str) -> bool:
    """Host paths, USB and other host devices (D86): cluster admins, and project admins
    whose grant a cluster admin marked for them."""
    if user.role == "admin":
        return True
    return user.grants.get(project) == "admin" and project in user.host_devices


# device types that reach the host's hardware; host-path disks too (``is_host_device``)
HOST_TYPES = frozenset({"usb", "pci", "gpu", "unix-char", "unix-block", "unix-hotplug"})


def is_host_device(dev: dict[str, str]) -> bool:
    """A device that uses the host directly: a hardware type, or a disk whose source is a
    host path (not a pool volume, Ceph or cloud-init)."""
    if dev.get("type") in HOST_TYPES:
        return True
    return dev.get("type") == "disk" and not dev.get("pool") and dev.get("source", "")[:1] == "/"


def host_device_changes(
    new: dict[str, dict[str, str]], old: dict[str, dict[str, str]]
) -> list[str]:
    """The host devices ``new`` adds or changes compared with ``old``. Keeping ones a
    cluster admin added is fine; removing any is too."""
    return sorted(n for n, d in new.items() if is_host_device(d) and old.get(n) != d)


@dataclass(frozen=True)
class Scope:
    """The projects a user may see: ``None`` for every project (a global role)."""

    projects: frozenset[str] | None

    def sees(self, project: str) -> bool:
        return self.projects is None or project in self.projects

    @property
    def everything(self) -> bool:
        """A global role: no filtering at all."""
        return self.projects is None

    def require(self, project: str) -> None:
        """Refuse a project the user can't see, as "not found" (it mustn't reveal what
        exists)."""
        if not self.sees(project):
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="not found")

    def keep(self, items: list[dict[str, Any]], key: str = "project") -> list[dict[str, Any]]:
        """The items in the user's projects (each names its project under ``key``)."""
        if self.projects is None:
            return items
        return [i for i in items if i.get(key) in self.projects]

    @classmethod
    def of(cls, user: User) -> Scope:
        return cls(None if user.role != "project" else frozenset(user.grants))
