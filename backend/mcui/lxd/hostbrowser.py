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
- Helpers aren't updated in place (no network, nothing to keep): a stopped helper older
  than ``browse_refresh_days``, or made from another image than ``browse_image``, is made
  again from the current image when next used, and images no helper uses are deleted
  (D97). ``rebuild_all`` does it now, for all of them.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from mcui.config import HostDevicesSettings
from mcui.lxd import operations
from mcui.lxd.client import LXDClient, LXDError
from mcui.lxd.remotes import REMOTES

log = logging.getLogger(__name__)

MARK_KEY, MARK = "user.mcui.role", "host-browser"
IMAGE_KEY = "user.mcui.image"  # the browse_image a helper was made from
HOST = "/host"
# type/size/mtime/path; a file name can't contain "/", so the path is what's left
STAT_FORMAT = "%F/%s/%Y/%n"
MAX_OUTPUT = 8 * 2**20  # bytes of a listing read from the helper
MAX_ENTRIES = 5000  # entries of a folder shown (D89)
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


def created(inst: dict[str, Any]) -> float | None:
    """An instance's ``created_at`` as a timestamp (LXD gives nanoseconds; Python takes
    up to microseconds)."""
    text = re.sub(r"(\.\d{6})\d+", r"\1", str(inst.get("created_at") or ""))
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _image(config: dict[str, str]) -> str:
    """What a helper was made from: "Alpine 3.24 (default), build 20261127_0052"."""
    text = config.get("image.description", "")
    serial = config.get("image.serial")
    return f"{text}, build {serial}" if text and serial else text


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
        wall: Callable[[], float] = time.time,
    ) -> None:
        self.lxd, self.cfg, self.clock, self.wall = lxd, cfg, clock, wall
        self.project = cfg.browse_project
        self.last_used: dict[str, float] = {}
        self.in_use: dict[str, int] = {}  # listings and resolves under way, by member
        self.remake: set[str] = set()  # members whose helper is made again once it stops
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
                {
                    "name": i["name"],
                    "member": i.get("location", ""),
                    "status": i.get("status", ""),
                    "image": _image(i.get("config") or {}),
                    "created_at": created(i),
                    "stale": self._stale(i),
                }
                for i in insts or []
            ]
        return {
            "project": self.project,
            "exists": True,
            "ours": self._ours(p),
            "helpers": helpers,
            "image": self.cfg.browse_image,
            "refresh_days": self.cfg.browse_refresh_days,
            "image_warning": self._image_warning(),
        }

    def _image_warning(self) -> str | None:
        """The weekly CI check watches the default image only (D97 review)."""
        default = type(self.cfg)().browse_image
        image = self.cfg.browse_image
        if not re.fullmatch(r"alpine/\d+\.\d+", image):
            return (
                f"browse_image {image} isn't a numbered Alpine release (edge is a development "
                f"branch): {default} is the default, and the one the project's checks watch"
            )
        if image != default:
            return f"browse_image {image} isn't the default ({default}), which the checks watch"
        return None

    def _stale(self, inst: dict[str, Any]) -> bool:
        """Made long enough ago, or from another image: to be made again."""
        if (inst.get("config") or {}).get(IMAGE_KEY) != self.cfg.browse_image:
            return True
        at = created(inst)
        return at is None or self.wall() - at > self.cfg.browse_refresh_days * 86400

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
                    # LXD makes instances from its cached copy of an alias; this keeps that
                    # copy refreshed from the image server (D97 review)
                    "images.auto_update_cached": "true",
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
            old = None
            if (
                inst is not None
                and inst.get("status") == "Stopped"
                and (self._stale(inst) or member in self.remake)
            ):
                # stopped, so nobody's using it: made again from the current image
                log.info(
                    "making the browser helper on %s again from %s", member, self.cfg.browse_image
                )
                old = (inst.get("config") or {}).get("volatile.base_image")
                await operations.run(self.lxd, "DELETE", f"/1.0/instances/{name}", params=self._p())
                inst = None
            if inst is None:
                await self._create(name, member, old)
                self.remake.discard(member)
            elif inst.get("status") == "Frozen":
                await self._state(name, "unfreeze")
            elif inst.get("status") != "Running":
                await self._state(name, "start")
            self.last_used[member] = self.clock()  # under the lock: stop_idle checks it
            self.in_use[member] = self.in_use.get(member, 0) + 1
        return name

    def _done(self, member: str) -> None:
        """A listing or resolve through the member's helper is over."""
        left = self.in_use.get(member, 1) - 1
        if left > 0:
            self.in_use[member] = left
        else:
            self.in_use.pop(member, None)

    async def _state(self, name: str, action: str) -> None:
        await operations.run(
            self.lxd,
            "PUT",
            f"/1.0/instances/{name}/state",
            params=self._p(),
            json={"action": action},
        )

    async def _create(self, name: str, member: str, old: str | None) -> None:
        """Make the member's helper from ``browse_image``. If that fails (the image server
        unreachable, say) and it's being made again, from the image the old one was made
        from, still in the project: browsing keeps working, and it's tried again on its
        next start (D97 review)."""
        body = self._body(name, await self._source())
        params = {**self._p(), "target": member}
        try:
            await operations.run(self.lxd, "POST", "/1.0/instances", params=params, json=body)
        except LXDError as e:
            if old is None:
                raise
            log.warning("browser helper on %s: %s; made from its old image", member, e)
            again = self._body(name, {"type": "image", "fingerprint": old})
            again["config"][IMAGE_KEY] = ""  # stale: made again from browse_image next time
            await operations.run(self.lxd, "POST", "/1.0/instances", params=params, json=again)
            return
        try:
            await self._prune()
        except LXDError as e:  # made; tidying up can wait for the next one
            log.warning("deleting old browser helper images: %s", e)

    def _body(self, name: str, source: dict[str, str]) -> dict[str, Any]:
        return {
            "name": name,
            "type": "container",
            "profiles": ["default"],
            "source": source,
            "config": {
                "limits.cpu": "1",
                "limits.memory": "64MiB",
                "boot.autostart": "false",
                "cluster.evacuate": "stop",
                IMAGE_KEY: self.cfg.browse_image,
            },
            "devices": {"host": {"type": "disk", "source": "/", "path": HOST, "readonly": "true"}},
            "start": True,
        }

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
            # not required: an NFS share unmounted later mustn't stop the helper starting
            mount_device(m): {
                "type": "disk",
                "source": m,
                "path": HOST + m,
                "readonly": "true",
                "required": "false",
            }
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
            out[fd] = await self._read_capped(log_path)
            await self.lxd.raw("DELETE", log_path, params=self._p())
        return int(md.get("return", -1)), out.get("1", ""), out.get("2", "")

    async def _read_capped(self, log_path: str) -> str:
        """At most ``MAX_OUTPUT`` bytes of a recorded output, whole lines only: a folder
        with millions of entries mustn't fill the server's memory (D89)."""
        resp = await self.lxd.open_stream("GET", log_path, params=self._p())
        buf = bytearray()
        try:
            async for chunk in resp.aiter_bytes():
                buf += chunk
                if len(buf) >= MAX_OUTPUT:
                    del buf[buf.rfind(b"\n", 0, MAX_OUTPUT) + 1 :]  # a cut line could mislead
                    break
        finally:
            await resp.aclose()
        return buf.decode("utf-8", "replace")

    async def listing(self, member: str, path: str, mounts: list[str]) -> list[Entry]:
        """The folder ``path`` on ``member`` (normalised and checked by the caller)."""
        name = await self._ensure_helper(member)
        try:
            await self._mount_along(name, path, mounts)
            target = HOST + (path if path != "/" else "/")
            cmd = ["find", target, "-mindepth", "1", "-maxdepth", "1"]
            stat = [*cmd, "-exec", "stat", "-c", STAT_FORMAT, "{}", "+"]
            rc, out, err = await self._exec(name, stat)
        finally:
            self._done(member)
        if rc != 0 and not out:
            if "No such file" in err:
                raise BrowseError(f"{path} doesn't exist on {member}", 404)
            if "Permission denied" in err:
                raise BrowseError(f"{path} on {member} isn't readable by ordinary users", 403)
            raise BrowseError(f"listing {path} on {member} failed: {err.strip()[:200]}", 502)
        self.last_used[member] = self.clock()  # a long listing counts as use
        return parse_listing(out)

    async def resolve(self, member: str, path: str, mounts: list[str]) -> str:
        """``path`` on ``member`` with its symlinks resolved, as the helper sees it: a
        project admin's hidden and allowed folders are checked against this too, since a
        link planted in a writable share could point into one (D90). A link leaving the
        member's folders (an absolute target resolves inside the helper) is refused."""
        name = await self._ensure_helper(member)
        try:
            await self._mount_along(name, path, mounts)
            # no "--": busybox realpath takes it for a file (seen live); the path starts /host
            rc, out, err = await self._exec(name, ["realpath", HOST + path])
        finally:
            self._done(member)
        real = out.strip()
        if rc != 0 or not real:
            raise BrowseError(f"{path} doesn't exist on {member}", 404)
        if real != HOST and not real.startswith(HOST + "/"):
            raise BrowseError(f"{path} on {member} is a link out of the host's folders", 403)
        return real[len(HOST) :] or "/"

    async def _prune(self) -> list[str]:
        """Delete the project's images no helper was made from (an older copy of the
        image, kept by LXD after a newer one was used). Only in the app's project with
        images of its own: without them the listing would be the default project's."""
        p = await self._project()
        config = (p or {}).get("config") or {}
        if p is None or not self._ours(p) or config.get("features.images") != "true":
            return []
        insts = await self.lxd.get("/1.0/instances", recursion=1, **self._p()) or []
        used = {(i.get("config") or {}).get("volatile.base_image") for i in insts}
        gone = []
        for im in await self.lxd.get("/1.0/images", recursion=1, **self._p()) or []:
            if im["fingerprint"] not in used:
                path = f"/1.0/images/{im['fingerprint']}"
                try:
                    await operations.run(self.lxd, "DELETE", path, params=self._p())
                    gone.append(im["fingerprint"])
                except LXDError as e:  # in use meanwhile, say: next time
                    log.warning("deleting an old browser helper image: %s", e)
        return gone

    async def rebuild_all(self) -> tuple[int, list[str]]:
        """Delete every helper and the project's images now, so each is made again from
        the image server's newest build when next used (LXD would otherwise use its cached
        copy, D97 review). A helper in use is made again once it stops. Goes on past a
        member that refuses; returns (deleted or marked, failures)."""
        async with self._lock:
            p = await self._project()
            if p is None or not self._ours(p):
                return 0, []
            insts = await self.lxd.get("/1.0/instances", recursion=1, **self._p()) or []
            done, failed = 0, []
            for inst in insts:
                member = inst.get("location", "")
                if self.in_use.get(member):
                    self.remake.add(member)  # browsing right now: when it's stopped
                    done += 1
                    continue
                try:
                    await self._delete(inst)
                    self.last_used.pop(member, None)
                    done += 1
                except LXDError as e:
                    failed.append(f"{inst['name']}: {e}")
            try:
                await self._prune()
            except LXDError as e:
                failed.append(f"images: {e}")
            return done, failed

    async def _delete(self, inst: dict[str, Any]) -> None:
        path = f"/1.0/instances/{inst['name']}"
        if inst.get("status") != "Stopped":  # running or frozen: LXD won't delete either
            await operations.run(
                self.lxd,
                "PUT",
                f"{path}/state",
                params=self._p(),
                json={"action": "stop", "force": True},
            )
        await operations.run(self.lxd, "DELETE", path, params=self._p())

    async def stop_idle(self) -> list[str]:
        """Stop the helpers nobody has used for ``browse_idle_minutes``."""
        limit = self.cfg.browse_idle_minutes * 60
        stopped = []
        for member, at in list(self.last_used.items()):
            if self.clock() - at < limit:
                continue
            async with self._lock:  # a listing that just started keeps it (D90)
                if self.last_used.get(member) != at:
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
        return stopped + await self._stop_unknown()

    async def _stop_unknown(self) -> list[str]:
        """Helpers running that the app isn't timing (left running across a restart):
        stopped, so they don't run on unpatched and get made again (D97 review)."""
        async with self._lock:
            p = await self._project()
            if p is None or not self._ours(p):
                return []
            stopped = []
            for inst in await self.lxd.get("/1.0/instances", recursion=1, **self._p()) or []:
                member = inst.get("location", "")
                if inst.get("status") == "Stopped" or member in self.last_used:
                    continue
                try:
                    await self._delete(inst) if self._stale(inst) else await operations.run(
                        self.lxd,
                        "PUT",
                        f"/1.0/instances/{inst['name']}/state",
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
                await self._delete(inst)
            # by fingerprint: the listed URLs can carry ?project=, which check_path refuses
            for im in await self.lxd.get("/1.0/images", recursion=1, **self._p()) or []:
                path = f"/1.0/images/{im['fingerprint']}"
                await operations.run(self.lxd, "DELETE", path, params=self._p())
            await operations.run(self.lxd, "DELETE", f"/1.0/projects/{self.project}")
            self.last_used.clear()
            return True
