"""Ceph dashboard (MO-7, MO-7a): the current state from the last ceph-mgr scrape,
and the busiest RBD images mapped to LXD instances and volumes (D56)."""

from __future__ import annotations

import contextlib
import time
from typing import Annotated, Any

from fastapi import APIRouter, Query

from mcui.api.deps import ClusterReadDep, ServicesDep
from mcui.api.metrics import StoreDep
from mcui.lxd.client import LXDError
from mcui.metrics.ceph_view import rbd_owner, summarize
from mcui.metrics.top import RbdMetric, rbd_top

router = APIRouter(prefix="/api/ceph")


@router.get("")
async def ceph_state(_: ClusterReadDep, services: ServicesDep) -> dict[str, Any]:
    c = services.collector
    if services.ceph is None or c is None:
        return {"enabled": False}
    last = c.ceph_last
    if last is None:
        return {"enabled": True, "ok": False, "error": "not scraped yet"}
    if not last.ok:
        return {"enabled": True, "ok": False, "error": last.error, "scraped_at": c.ceph_at}
    return {
        "enabled": True,
        "ok": True,
        "mgr": last.url,
        "scraped_at": c.ceph_at,
        **summarize(last.exposition),
    }


@router.get("/rbd")
async def rbd(
    _: ClusterReadDep,
    services: ServicesDep,
    store: StoreDep,
    metric: RbdMetric = "write_bytes",
    range_: Annotated[int, Query(alias="range", ge=60, le=31 * 86400)] = 3600,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
) -> dict[str, Any]:
    end = int(time.time())
    rows = await rbd_top(store, metric, end - range_, end, limit)
    projects: list[str] = []
    if services.lxd is not None:
        # names in the default project still map without the list
        with contextlib.suppress(LXDError):
            urls = await services.lxd.get("/1.0/projects") or []
            projects = [str(u).rsplit("/", 1)[-1] for u in urls]
    return {
        "metric": metric,
        "range": range_,
        "rows": [{**r, "owner": rbd_owner(r["image"], projects)} for r in rows],
    }
