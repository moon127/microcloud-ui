"""Background collection into the metric store (MO-1, MO-2, MO-4, MO-6, MO-8, D35)."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from typing import Any

from mcui.config import Settings
from mcui.lxd.client import LXDClient, LXDError
from mcui.lxd.server import members
from mcui.metrics.ceph import CephScraper
from mcui.metrics.live import LiveMetrics
from mcui.metrics.select import (
    ceph_samples,
    host_networks,
    lxd_samples,
    member_state_samples,
    network_state_samples,
)
from mcui.metrics.store import MetricStore

log = logging.getLogger(__name__)
COMPACT_INTERVAL = 300


@dataclass
class TaskStatus:
    last_run: float | None = None
    last_ok: float | None = None
    last_error: str | None = None
    samples: int = 0
    duration: float = 0.0


class Collector:
    """Three loops: metrics scrape (LXD + Ceph), member/network state, compaction."""

    def __init__(
        self,
        api: LXDClient,
        live: LiveMetrics,
        store: MetricStore,
        settings: Settings,
        ceph: CephScraper | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.api = api
        self.live = live
        self.store = store
        self.settings = settings
        self.ceph = ceph
        self.clock = clock
        self.status: dict[str, TaskStatus] = {
            k: TaskStatus() for k in ("lxd", "ceph", "state", "compact")
        }
        self._tasks: list[asyncio.Task[None]] = []

    # one round of each job

    async def collect_lxd(self) -> int:
        n = 0
        errors = []
        for member, scrape in await self.live.refresh():
            if scrape.ok:
                n += await self.store.write(
                    int(scrape.at), lxd_samples(member.name, scrape.exposition)
                )
            else:
                errors.append(f"{member.name}: {scrape.error}")
        if errors:
            raise RuntimeError("; ".join(errors))
        return n

    async def collect_ceph(self) -> int:
        if self.ceph is None:
            return 0
        res = await self.ceph.scrape(self.live.members)
        if not res.ok:
            raise RuntimeError(res.error)
        return await self.store.write(int(self.clock()), ceph_samples(res.exposition))

    async def collect_state(self) -> int:
        n = 0
        errors = []
        for m in self.live.members or await members(self.api):
            try:
                try:
                    state = await self.api.get(f"/1.0/cluster/members/{m.name}/state")
                except LXDError as e:
                    if e.status != 404:  # 404: standalone server, no member state
                        raise
                else:
                    n += await self.store.write(
                        int(self.clock()), member_state_samples(m.name, state)
                    )
                nets = await self.api.get("/1.0/networks", recursion=1, target=m.name)
                for net in host_networks(nets):
                    st = await self.api.get(f"/1.0/networks/{net}/state", target=m.name)
                    n += await self.store.write(
                        int(self.clock()), network_state_samples(m.name, net, st)
                    )
            except LXDError as e:
                errors.append(f"{m.name}: {e}")
        if errors:
            raise RuntimeError("; ".join(errors))
        return n

    async def compact(self) -> int:
        stats = await self.store.compact()
        return stats["packed"]

    # scheduling

    async def _guard(self, name: str, job: Callable[[], Awaitable[int]]) -> None:
        st = self.status[name]
        st.last_run = self.clock()
        t0 = time.monotonic()
        try:
            st.samples = await job()
        except Exception as e:  # keep the loop alive; surface the error in status
            st.last_error = str(e) or type(e).__name__
            log.warning("metrics %s: %s", name, st.last_error)
        else:
            st.last_ok = st.last_run
            st.last_error = None
        st.duration = time.monotonic() - t0

    async def _scrape_round(self) -> None:
        await self._guard("lxd", self.collect_lxd)
        if self.ceph is not None:
            await self._guard("ceph", self.collect_ceph)

    async def _loop(self, interval: float, round_: Callable[[], Awaitable[None]]) -> None:
        while True:
            started = time.monotonic()
            await round_()
            await asyncio.sleep(max(0.0, interval - (time.monotonic() - started)))

    def start(self) -> None:
        self.live.passive = True
        m = self.settings.metrics
        self._tasks = [
            asyncio.create_task(self._loop(m.interval_seconds, self._scrape_round)),
            asyncio.create_task(
                self._loop(
                    m.state_interval_seconds, lambda: self._guard("state", self.collect_state)
                )
            ),
            asyncio.create_task(
                self._loop(COMPACT_INTERVAL, lambda: self._guard("compact", self.compact))
            ),
        ]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        self.live.passive = False

    @property
    def running(self) -> bool:
        return bool(self._tasks)

    def status_dict(self) -> dict[str, Any]:
        return {k: asdict(v) for k, v in self.status.items()}
