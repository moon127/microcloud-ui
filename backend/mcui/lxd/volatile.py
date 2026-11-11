"""LXD's own ``volatile.*`` keys must survive a config PUT (D55).

LXD lets a PUT drop them, and some can't be re-created: seen live, a custom
volume whose ``volatile.uuid`` went missing could no longer be snapshotted.
"""

from __future__ import annotations

from collections.abc import Mapping


def keep_volatile(current: Mapping[str, str] | None, new: Mapping[str, str]) -> dict[str, str]:
    """``new`` plus any current ``volatile.*`` key it leaves out."""
    out = dict(new)
    for k, v in (current or {}).items():
        if k.startswith("volatile.") and k not in out:
            out[k] = v
    return out
