"""Top-N instances by CPU, memory, network or disk over a window (MO-5)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from mcui.metrics.store import Fn, MetricStore

TopMetric = Literal["cpu", "memory", "net", "disk"]
BY = ("project", "name")

# metric -> (series summed, fn, unit)
PARTS: dict[str, tuple[tuple[str, ...], Fn, str]] = {
    "cpu": (("mcui_instance_cpu_busy_seconds_total",), "rate", "percent"),
    "memory": (("mcui_instance_memory_used_bytes",), "value", "bytes"),
    "net": (
        ("lxd_network_receive_bytes_total", "lxd_network_transmit_bytes_total"),
        "rate",
        "bytes_per_second",
    ),
    "disk": (
        ("lxd_disk_read_bytes_total", "lxd_disk_written_bytes_total"),
        "rate",
        "bytes_per_second",
    ),
}


def _mean(points: list[tuple[int, float]]) -> float | None:
    return sum(v for _, v in points) / len(points) if points else None


async def _means(
    store: MetricStore, name: str, match: Mapping[str, str], start: int, end: int, fn: Fn
) -> dict[tuple[str, ...], float]:
    res = await store.query(name, match, start, end, fn=fn, by=BY)
    out = {}
    for s in res.series:
        m = _mean(s.points)
        if m is not None:
            out[tuple(s.labels[k] for k in BY)] = m
    return out


async def top(
    store: MetricStore,
    metric: TopMetric,
    start: int,
    end: int,
    limit: int = 10,
    match: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    names, fn, unit = PARTS[metric]
    match = dict(match or {})
    totals: dict[tuple[str, ...], float] = {}
    for name in names:
        for key, v in (await _means(store, name, match, start, end, fn)).items():
            totals[key] = totals.get(key, 0.0) + v
    if metric == "cpu":
        cpus = await _means(store, "mcui_instance_cpus", match, start, end, "value")
        totals = {k: v / cpus[k] * 100 for k, v in totals.items() if cpus.get(k)}
    rows = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    return {
        "metric": metric,
        "unit": unit,
        "start": start,
        "end": end,
        "rows": [{"project": k[0], "name": k[1], "value": v} for k, v in rows],
    }
