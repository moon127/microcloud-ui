"""The opt-in folder browser for a member's own files (D87).

LXD has no API for a member's files, only for an instance's. So the app keeps a small
helper container per member, in a project of its own (``mcui-server`` by default, marked
``user.mcui.role=host-browser``), with the member's ``/`` mounted read-only at ``/host``.
Listing ``/host/srv`` in the helper lists ``/srv`` on the member.

- The helper is unprivileged and the mount isn't shifted, so it sees only what any
  ordinary user on the member could list: not ``/root`` or private home folders.
- Read-only bind mounts can't be recursive (the kernel refuses), so other filesystems
  mounted below ``/`` (``/boot``, an NFS share) look empty. When browsing reaches one the
  member's node_exporter reports, it's added to the helper read-only, while it runs.
- Helpers start on first use and stop after ``browse_idle_minutes``. Switching the browser
  off deletes the helpers, their image and the project, but only a project carrying the
  marker. All checked live on LXD 6.10.1 (2026-11-27).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from mcui.config import HostDevicesSettings
from mcui.lxd import operations
from mcui.lxd.client import LXDClient, LXDError
from mcui.lxd.remotes import REMOTES

log = logging.getLogger(__name__)

MARK_KEY, MARK = "user.mcui.role", "host-browser"
HOST = "/host"
# type/size/mtime/path; a file name can't contain "/", so the path is what's left
STAT_FORMAT = "%F/%s/%Y/%n"
TYPES = {"directory": "dir", "regular file": "file", "regular empty file": "file"}


class BrowseError(Exception):
    """A listing the member refused or that doesn't exist; ``status`` for HTTP."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def helper_name(member: str) -> str:
    """``browse-<member>``, as an instance name LXD accepts."""
    slug = re.sub(r"[^a-z0-9-]+", "-", member.lower()).strip("-")
    return f"browse-{slug}"[:63].rstrip("-")


def mount_device(mount: str) -> str:
    return "m-" + hashlib.sha256(mount.encode()).hexdigest()[:12]


@dataclass
class Entry:
    name: str
    type: str  # dir, file, link or other
    size: int
    mtime: int


def parse_listing(out: str) -> list[Entry]:
    entries = []
    for line in out.splitlines():
        parts = line.split("/", 3)
        if len(parts) != 4 or not parts[1].isdigit() or not parts[2].isdigit():
            continue  # a name with a newline in it: left out
        kind, size, mtime, path = parts
        name = path.rstrip("/").rsplit("/", 1)[-1]
        t = TYPES.get(kind, "link" if kind == "symbolic link" else "other")
        entries.append(Entry(name, t, int(size), int(mtime)))
    return sorted(entries, key=lambda e: (e.type != "dir", e.name))


class HostBrowser:
    def __init__(
        self,
        lxd: LXDClient,
        cfg: HostDevicesSettings,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.lxd, self.cfg, self.clock = lxd, cfg, clock
        self.project = cfg.browse_project
        self.last_used: dict[str, float] = {}
        self._lock = asyncio.Lock()

    def _p(self) -> dict[str, str]:
        return {"project": self.project}

    async def _project(self) -> dict[str, Any] | None:
        try:
            p: dict[str, Any] = await self.lxd.get(f"/1.0/projects/{self.project}")
        except LXDError as e:
            if e.status == 404:
                return None
            raise
        return p

    def _ours(self, p: dict[str, Any]) -> bool:
        return (p.get("config") or {}).get(MARK_KEY) == MARK

    async def status(self) -> dict[str, Any]:
        p = await self._project()
        if p is None:
            return {"project": self.project, "exists": False, "ours": False, "helpers": []}
        helpers = []
        if self._ours(p):
            insts = await self.lxd.get("/1.0/instances", recursion=1, project=self.project)
            helpers = [
                {"name": i["name"], "member": i.get("location", ""), "status": i.get("status", "")}
                for i in insts or []
            ]
        return {"project": self.project, "exists": True, "ours": self._ours(p), "helpers": helpers}

    async def _pool(self) -> str:
        if self.cfg.browse_pool:
            return self.cfg.browse_pool
        prof = await self.lxd.get("/1.0/profiles/default", project="default")
        pool = ((prof.get("devices") or {}).get("root") or {}).get("pool")
        if not pool:
            raise BrowseError("no root disk pool in the default profile: set browse_pool", 409)
        return str(pool)

    async def _ensure_project(self) -> None:
        p = await self._project()
        if p is not None:
            if not self._ours(p):
                raise BrowseError(
                    f"project {self.project} exists and isn't the app's (no {MARK_KEY}={MARK}):"
                    " choose another browse_project",
                    409,
                )
            return
        await operations.run(
            self.lxd,
            "POST",
            "/1.0/projects",
            json={
                "name": self.project,
                "description": "microcloud-ui: folder browser helpers (one per member)",
                "config": {
                    MARK_KEY: MARK,
                    "features.images": "true",
                    "features.profiles": "true",
                    "features.storage.volumes": "true",
                },
            },
        )
        root = {"type": "disk", "path": "/", "pool": await self._pool(), "size": "1GiB"}
        await operations.run(
            self.lxd,
            "PUT",
            "/1.0/profiles/default",
            params=self._p(),
            json={"description": "browser helpers: no network", "devices": {"root": root}},
        )

    async def prepare(self) -> None:
        """The project and its profile, so a name clash shows when switching it on."""
        async with self._lock:
            await self._ensure_project()

    async def _source(self) -> dict[str, str]:
        info = await self.lxd.get("/1.0")
        if "image_registries" in (info.get("api_extensions") or []):
            return {"type": "image", "alias": self.cfg.browse_image, "image_registry": "images"}
        return {
            "type": "image",
            "alias": self.cfg.browse_image,
            "server": REMOTES["images"],
            "protocol": "simplestreams",
            "mode": "pull",
        }

    async def _ensure_helper(self, member: str) -> str:
        """The member's helper, created and running."""
        name = helper_name(member)
        async with self._lock:
            await self._ensure_project()
            try:
                inst = await self.lxd.get(f"/1.0/instances/{name}", **self._p())
            except LXDError as e:
                if e.status != 404:
                    raise
                inst = None
            if inst is None:
                body = {
                    "name": name,
                    "type": "container",
                    "profiles": ["default"],
                    "source": await self._source(),
                    "config": {
                        "limits.cpu": "1",
                        "limits.memory": "64MiB",
                        "boot.autostart": "false",
                        "cluster.evacuate": "stop",
                    },
                    "devices": {
                        "host": {"type": "disk", "source": "/", "path": HOST, "readonly": "true"}
                    },
                    "start": True,
                }
                await operations.run(
                    self.lxd,
                    "POST",
                    "/1.0/instances",
                    params={**self._p(), "target": member},
                    json=body,
                )
            elif inst.get("status") != "Running":
                await operations.run(
                    self.lxd,
                    "PUT",
                    f"/1.0/instances/{name}/state",
                    params=self._p(),
                    json={"action": "start"},
                )
        self.last_used[member] = self.clock()
        return name

    async def _mount_along(self, name: str, path: str, mounts: list[str]) -> None:
        """Add the member's filesystems on the way to ``path`` (nested ones in order)."""
        along = sorted(
            (m for m in mounts if m != "/" and (path == m or path.startswith(m.rstrip("/") + "/"))),
            key=len,
        )
        if not along:
            return
        inst = await self.lxd.get(f"/1.0/instances/{name}", **self._p())
        have = inst.get("devices") or {}
        add = {
            mount_device(m): {"type": "disk", "source": m, "path": HOST + m, "readonly": "true"}
            for m in along
            if mount_device(m) not in have
        }
        if add:
            await operations.run(
                self.lxd,
                "PATCH",
                f"/1.0/instances/{name}",
                params=self._p(),
                json={"devices": add},
            )

    async def _exec(self, name: str, command: list[str]) -> tuple[int, str, str]:
        st = await operations.run(
            self.lxd,
            "POST",
            f"/1.0/instances/{name}/exec",
            params=self._p(),
            json={
                "command": command,
                "record-output": True,
                "interactive": False,
                "wait-for-websocket": False,
                "environment": {},
            },
        )
        md = ((st.operation or {}).get("metadata")) or {}
        out: dict[str, str] = {}
        for fd, log_path in (md.get("output") or {}).items():
            r = await self.lxd.raw("GET", log_path, params=self._p())
            out[fd] = r.text
            await self.lxd.raw("DELETE", log_path, params=self._p())
        return int(md.get("return", -1)), out.get("1", ""), out.get("2", "")

    async def listing(self, member: str, path: str, mounts: list[str]) -> list[Entry]:
        """The folder ``path`` on ``member`` (normalised and checked by the caller)."""
        name = await self._ensure_helper(member)
        await self._mount_along(name, path, mounts)
        target = HOST + (path if path != "/" else "/")
        cmd = ["find", target, "-mindepth", "1", "-maxdepth", "1"]
        rc, out, err = await self._exec(name, [*cmd, "-exec", "stat", "-c", STAT_FORMAT, "{}", "+"])
        if rc != 0 and not out:
            if "No such file" in err:
                raise BrowseError(f"{path} doesn't exist on {member}", 404)
            if "Permission denied" in err:
                raise BrowseError(f"{path} on {member} isn't readable by ordinary users", 403)
            raise BrowseError(f"listing {path} on {member} failed: {err.strip()[:200]}", 502)
        return parse_listing(out)

    async def stop_idle(self) -> list[str]:
        """Stop the helpers nobody has used for ``browse_idle_minutes``."""
        limit = self.cfg.browse_idle_minutes * 60
        stopped = []
        for member, at in list(self.last_used.items()):
            if self.clock() - at < limit:
                continue
            self.last_used.pop(member)
            try:
                await operations.run(
                    self.lxd,
                    "PUT",
                    f"/1.0/instances/{helper_name(member)}/state",
                    params=self._p(),
                    json={"action": "stop", "force": True},
                )
                stopped.append(member)
            except LXDError as e:
                log.warning("stopping the browser helper on %s: %s", member, e)
        return stopped

    async def remove_all(self) -> bool:
        """Delete the helpers, their images and the project. ``False``: nothing to do."""
        async with self._lock:
            p = await self._project()
            if p is None:
                return False
            if not self._ours(p):
                raise BrowseError(
                    f"project {self.project} isn't the app's (no {MARK_KEY}={MARK}): not deleted",
                    409,
                )
            for inst in await self.lxd.get("/1.0/instances", recursion=1, **self._p()) or []:
                path = f"/1.0/instances/{inst['name']}"
                if inst.get("status") == "Running":
                    await operations.run(
                        self.lxd,
                        "PUT",
                        f"{path}/state",
                        params=self._p(),
                        json={"action": "stop", "force": True},
                    )
                await operations.run(self.lxd, "DELETE", path, params=self._p())
            # by fingerprint: the listed URLs can carry ?project=, which check_path refuses
            for im in await self.lxd.get("/1.0/images", recursion=1, **self._p()) or []:
                path = f"/1.0/images/{im['fingerprint']}"
                await operations.run(self.lxd, "DELETE", path, params=self._p())
            await operations.run(self.lxd, "DELETE", f"/1.0/projects/{self.project}")
            self.last_used.clear()
            return True
