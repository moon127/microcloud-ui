"""Which of a member's folders project admins may browse and attach (D87).

Cluster admins see and attach anything. Project admins with the host-devices right (D86)
don't get the *hidden* folders: they're left out of listings, and browsing or attaching a
path under one is refused. The list comes from the app's saved setting, else the config
file's ``[host_devices] hidden_paths``, else ``DEFAULT_HIDDEN``. A project's
``restricted.devices.disk.paths``, when set, narrows browsing further to those folders
(LXD refuses attaching anything else anyway).

The check is on the path as written, like LXD's own ``restricted.devices.disk.paths``
(a lexical prefix match: ``filepath.Clean``, no symlinks resolved; LXD's source,
checked 2026-11-27). A symlink in a folder an instance can write to can point elsewhere
on the member, so allow only folders instances can't write to, or attach read-only. The
folder browser resolves links before its check (D90).
"""

from __future__ import annotations

import posixpath
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from mcui.auth.store import Store
from mcui.config import HostDevicesSettings

# personal files; host config and keys; snap state (LXD, MicroCeph and MicroOVN keys and
# certificates); /run (the LXD socket: mounting it hands over the cluster); service state;
# logs; kernel and devices; other users' temporary files; programs and libraries, spool
# (cron), caches and backups, where a writable mount runs code as root (D90)
DEFAULT_HIDDEN: tuple[str, ...] = (
    "/bin",
    "/sbin",
    "/lib",
    "/lib32",
    "/lib64",
    "/usr",
    "/var/spool",
    "/var/cache",
    "/var/backups",
    "/var/mail",
    "/home",
    "/root",
    "/etc",
    "/snap",
    "/var/snap",
    "/run",
    "/var/run",
    "/var/lib",
    "/var/log",
    "/proc",
    "/sys",
    "/dev",
    "/boot",
    "/tmp",  # noqa: S108 - a path to hide, not a temp file
    "/var/tmp",  # noqa: S108
)
# USB disks are offered from the member's inventory: never hidden
ALWAYS_SHOWN = ("/dev/disk/by-id/usb-",)

HIDDEN_KEY = "host_devices.hidden_paths"
BROWSE_KEY = "host_devices.browse"
Source = Literal["app", "config", "default"]


def normalize(path: str) -> str:
    """An absolute path without ``.``, ``..``, doubled or trailing slashes."""
    if not path.startswith("/") or "\0" in path:
        raise ValueError("an absolute path")
    norm = posixpath.normpath(path)
    return "/" + norm.lstrip("/")


def under(path: str, prefixes: Iterable[str]) -> bool:
    """Is ``path`` one of ``prefixes`` or inside one?"""
    p = normalize(path)
    for prefix in prefixes:
        q = normalize(prefix)
        if q == "/" or p == q or p.startswith(q + "/"):
            return True
    return False


def hidden(path: str, hidden_paths: Sequence[str]) -> bool:
    p = normalize(path)
    if any(p.startswith(a) for a in ALWAYS_SHOWN):
        return False
    return under(p, hidden_paths)


def exposes_hidden(path: str, hidden_paths: Sequence[str]) -> bool:
    """For attaching: is ``path`` hidden, or does it contain a hidden folder? Attaching
    ``/`` or ``/var`` would hand over ``/etc`` or ``/var/snap`` with it (found in the
    2026-11-27 review). Browsing still passes through such folders; listings leave the
    hidden ones out."""
    p = normalize(path)
    if hidden(p, hidden_paths):
        return True
    if any(p.startswith(a) for a in ALWAYS_SHOWN):
        return False
    return p == "/" or any(under(h, [p]) for h in hidden_paths)


@dataclass(frozen=True)
class HostFsConfig:
    hidden_paths: list[str]
    hidden_source: Source
    browse: bool
    browse_source: Source


def effective(cfg: HostDevicesSettings, store: Store) -> HostFsConfig:
    """The app's saved values win over the config file, which wins over the defaults."""
    saved_hidden = store.get_setting(HIDDEN_KEY)
    if saved_hidden is not None:
        paths, h_src = list(saved_hidden), "app"
    elif cfg.hidden_paths is not None:
        paths, h_src = list(cfg.hidden_paths), "config"
    else:
        paths, h_src = list(DEFAULT_HIDDEN), "default"
    saved_browse = store.get_setting(BROWSE_KEY)
    if saved_browse is not None:
        browse, b_src = bool(saved_browse), "app"
    else:
        browse, b_src = cfg.browse, ("config" if cfg.browse else "default")
    return HostFsConfig(
        [normalize(p) for p in paths],
        h_src,  # type: ignore[arg-type]
        browse,
        b_src,  # type: ignore[arg-type]
    )
