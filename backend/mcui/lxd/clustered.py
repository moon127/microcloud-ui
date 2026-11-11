"""Creating objects that LXD keeps per member (local networks and storage pools).

In a cluster LXD wants a pending definition on every member first (carrying
that member's own keys such as ``parent`` or ``source``), then the
cluster-wide definition. If any step fails, the half-made object is deleted so
the name is free again (D52). On a standalone server there is one step and the
member's keys go in the config.
"""

from __future__ import annotations

import contextlib
from collections.abc import Mapping
from typing import Any

from mcui.lxd import operations
from mcui.lxd.client import LXDClient, LXDError
from mcui.lxd.server import members, server_info


async def create(
    lxd: LXDClient,
    collection: str,
    ident: Mapping[str, str],
    description: str,
    config: Mapping[str, str],
    member_config: Mapping[str, Mapping[str, str]],
    *,
    per_member: bool,
    params: Mapping[str, Any] | None = None,
) -> operations.Started:
    """POST ``ident`` (name and type/driver) to ``collection``, in two steps if needed."""
    info = await server_info(lxd)
    params = dict(params or {})
    pending = info.clustered and per_member
    name = ident["name"]
    if pending:
        try:
            for m in await members(lxd, info):
                await operations.start(
                    lxd,
                    "POST",
                    collection,
                    json={**ident, "config": dict(member_config.get(m.name, {}))},
                    params={**params, "target": m.name},
                )
        except LXDError:
            await _forget(lxd, f"{collection}/{name}", params)
            raise
    merged = dict(config)
    if not info.clustered:
        for cfg in member_config.values():
            merged.update(cfg)
    try:
        return await operations.start(
            lxd,
            "POST",
            collection,
            json={**ident, "description": description, "config": merged},
            params=params,
        )
    except LXDError:
        if pending:
            await _forget(lxd, f"{collection}/{name}", params)
        raise


async def _forget(lxd: LXDClient, path: str, params: Mapping[str, Any]) -> None:
    # nothing was pending, or it's gone already
    with contextlib.suppress(LXDError):
        await operations.start(lxd, "DELETE", path, params=dict(params))
