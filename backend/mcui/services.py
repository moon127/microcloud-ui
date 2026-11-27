"""Long-lived objects shared by the API: settings, store, LXD clients, live metrics."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field

from mcui.auth.store import Store
from mcui.config import Settings
from mcui.hostfs import HostFsConfig, effective
from mcui.lxd.client import LXDClient
from mcui.lxd.hostbrowser import HostBrowser
from mcui.lxd.instances import InstanceIndex
from mcui.lxd.remotes import RemoteCatalog
from mcui.metrics.ceph import CephScraper
from mcui.metrics.collector import Collector
from mcui.metrics.live import LiveMetrics
from mcui.metrics.node import NodeScraper
from mcui.metrics.scrape import Scraper
from mcui.metrics.store import H1, M1, RAW, MetricStore
from mcui.streams import StreamRegistry

log = logging.getLogger(__name__)
HOUSEKEEPING_SECONDS = 60.0


@dataclass
class Services:
    settings: Settings
    store: Store
    lxd: LXDClient | None = None
    metrics_lxd: LXDClient | None = None
    live: LiveMetrics | None = None
    metrics: MetricStore | None = None
    collector: Collector | None = None
    ceph: CephScraper | None = None
    instances: InstanceIndex | None = None
    remotes: RemoteCatalog | None = None
    node: NodeScraper | None = None
    streams: StreamRegistry = field(default_factory=StreamRegistry)
    browser: HostBrowser | None = None
    _housekeeping: asyncio.Task[None] | None = None

    def hostfs(self) -> HostFsConfig:
        """Hidden folders and the browser switch, as saved in the app or configured (D87)."""
        return effective(self.settings.host_devices, self.store)

    async def browser_housekeeping(self) -> None:
        """Stop idle helpers; with the browser switched off, remove what's left of it (a
        config change while the app was down)."""
        if self.browser is None:
            return
        try:
            if self.hostfs().browse:
                await self.browser.stop_idle()
            elif await self.browser.remove_all():
                log.info("folder browser off: removed project %s", self.browser.project)
        except Exception as e:  # noqa: BLE001 - keep the loop alive; LXD may be away
            log.warning("folder browser housekeeping: %s", e)

    async def _housekeep(self) -> None:
        while True:
            await self.browser_housekeeping()
            await asyncio.sleep(HOUSEKEEPING_SECONDS)

    async def start(self) -> None:
        if self.collector is not None and self.settings.metrics.collect:
            self.collector.start()
        if self.browser is not None:
            self._housekeeping = asyncio.create_task(self._housekeep())

    async def aclose(self) -> None:
        if self._housekeeping is not None:
            self._housekeeping.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._housekeeping
        if self.collector is not None:
            await self.collector.stop()
        for c in {id(c): c for c in (self.lxd, self.metrics_lxd) if c is not None}.values():
            await c.aclose()
        for scraper in (self.ceph, self.node):
            if scraper is not None:
                await scraper.aclose()
        if self.remotes is not None:
            await self.remotes.aclose()
        if self.metrics is not None:
            await self.metrics.aclose()
        self.store.close()


def open_metric_store(settings: Settings) -> MetricStore:
    m = settings.metrics
    retention = {
        RAW: m.retention_raw_hours * 3600,
        M1: m.retention_1m_days * 86400,
        H1: m.retention_1h_days * 86400,
    }
    return MetricStore.open(
        settings.server.data_dir, retention=retention, max_gap=4 * m.interval_seconds
    )


def lxd_clients(settings: Settings) -> tuple[LXDClient, LXDClient] | None:
    """Management and metrics clients (D5); ``None`` when LXD isn't configured."""
    lxd = settings.lxd
    pair = settings.metrics_cert
    if lxd is None or pair is None:
        return None
    api = LXDClient.from_files(
        lxd.endpoints,
        server_cert=lxd.server_cert,
        client_cert=lxd.client_cert,
        client_key=lxd.client_key,
        server_name=lxd.server_name,
        timeout=lxd.timeout_seconds,
    )
    metrics = LXDClient.from_files(
        lxd.endpoints,
        server_cert=lxd.server_cert,
        client_cert=pair[0],
        client_key=pair[1],
        server_name=lxd.server_name,
        timeout=lxd.timeout_seconds,
    )
    return api, metrics


def build_services(
    settings: Settings,
    *,
    store: Store | None = None,
    clients: tuple[LXDClient, LXDClient] | None = None,
    metrics_store: MetricStore | None = None,
) -> Services:
    store = store or Store.open(settings.server.data_dir)
    clients = clients or lxd_clients(settings)
    if clients is None:
        return Services(settings, store)
    api, metrics = clients
    live = LiveMetrics(api, Scraper(metrics, settings.metrics))
    history = metrics_store or open_metric_store(settings)
    ceph = CephScraper(settings.ceph) if settings.ceph.enabled else None
    node = NodeScraper(settings.node_exporter) if settings.node_exporter.enabled else None
    collector = Collector(api, live, history, settings, ceph, node)
    return Services(
        settings,
        store,
        api,
        metrics,
        live,
        history,
        collector,
        ceph,
        InstanceIndex(api),
        RemoteCatalog(),
        node,
        browser=HostBrowser(api, settings.host_devices),
    )
