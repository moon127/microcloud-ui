"""Private files: the SQLite databases hold password hashes, session tokens and the audit log."""

from __future__ import annotations

import contextlib
import stat
from pathlib import Path


def private_db(path: Path) -> None:
    """Make ``path`` (and its WAL files) readable by the owner only, whatever the umask.

    A container runs with umask 022, unlike the systemd unit's 0077 (D64). SQLite gives
    ``-wal``/``-shm`` the database file's mode, so creating the file 0600 first covers them.
    """
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.touch(mode=0o600, exist_ok=True)
    for p in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
        with contextlib.suppress(OSError):  # missing, or someone else's (e.g. a read by root)
            if p.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
                p.chmod(0o600)
