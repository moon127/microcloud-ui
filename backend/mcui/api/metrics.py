"""Metric history API: names, series, range queries, top-N, collector status (MO-2..MO-8)."""

from __future__ import annotations

import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from mcui.api.deps import ServicesDep, SessionDep
from mcui.metrics.store import RESOLUTIONS, Fn, MetricStore
from mcui.metrics.top import TopMetric, top

router = APIRouter(prefix="/api/metrics")

MAX_RANGE = 400 * 86400


async def get_store(services: ServicesDep) -> MetricStore:
    if services.metrics is None:
        raise HTTPException(status_code=503, detail="metric history is not enabled")
    return services.metrics


StoreDep = Annotated[MetricStore, Depends(get_store)]


def parse_match(match: list[str]) -> dict[str, str]:
    out = {}
    for m in match:
        k, sep, v = m.partition("=")
        if not sep or not k:
            raise HTTPException(status_code=400, detail=f"match must be label=value: {m!r}")
        out[k] = v
    return out


def time_range(start: int | None, end: int | None, range_: int) -> tuple[int, int]:
    end = int(time.time()) if end is None else end
    start = end - range_ if start is None else start
    if start >= end or end - start > MAX_RANGE:
        raise HTTPException(status_code=400, detail="invalid time range")
    return start, end


Match = Annotated[list[str], Query(description="label=value, repeatable")]


@router.get("/names")
async def names(_: SessionDep, store: StoreDep) -> list[dict[str, Any]]:
    return await store.names()


@router.get("/series")
async def series(
    _: SessionDep,
    store: StoreDep,
    name: str,
    match: Match = [],  # noqa: B006
) -> list[dict[str, str]]:
    return [labels for _, labels, _ in await store.find_series(name, parse_match(match))]


@router.get("/query")
async def query(
    _: SessionDep,
    store: StoreDep,
    name: str,
    match: Match = [],  # noqa: B006
    start: int | None = None,
    end: int | None = None,
    range: Annotated[int, Query(ge=60)] = 3600,  # noqa: A002
    fn: Fn = "value",
    res: int | None = None,
    by: Annotated[list[str] | None, Query(description="sum series by these labels")] = None,
) -> dict[str, Any]:
    if res is not None and res not in RESOLUTIONS:
        raise HTTPException(status_code=400, detail=f"res must be one of {RESOLUTIONS}")
    s, e = time_range(start, end, range)
    result = await store.query(name, parse_match(match), s, e, fn=fn, res=res, by=by)
    return {**result.as_dict(), "start": s, "end": e}


@router.get("/top")
async def top_n(
    _: SessionDep,
    store: StoreDep,
    metric: TopMetric,
    range: Annotated[int, Query(ge=60)] = 3600,  # noqa: A002
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
    project: str | None = None,
    member: str | None = None,
) -> dict[str, Any]:
    s, e = time_range(None, None, range)
    match = {k: v for k, v in (("project", project), ("member", member)) if v}
    return await top(store, metric, s, e, limit, match)


@router.get("/status")
async def status(_: SessionDep, services: ServicesDep, store: StoreDep) -> dict[str, Any]:
    c = services.collector
    return {
        "collecting": bool(c and c.running),
        "jobs": c.status_dict() if c else {},
        "store": await store.stats(),
    }
