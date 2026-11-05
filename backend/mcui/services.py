"""Long-lived objects shared by the API: settings, store, LXD clients, live metrics."""

from __future__ import annotations

from dataclasses import dataclass, field

from mcui.auth.store import Store
from mcui.config import Settings
from mcui.lxd.client import LXDClient
from mcui.lxd.instances import InstanceIndex
from mcui.lxd.remotes import RemoteCatalog
from mcui.metrics.ceph import CephScraper
from mcui.metrics.collector import Collector
from mcui.metrics.live import LiveMetrics
from mcui.metrics.scrape import Scraper
from mcui.metrics.store import H1, M1, RAW, MetricStore
from mcui.streams import StreamRegistry


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
    streams: StreamRegistry = field(default_factory=StreamRegistry)

    async def start(self) -> None:
        if self.collector is not None and self.settings.metrics.collect:
            self.collector.start()

    async def aclose(self) -> None:
        if self.collector is not None:
            await self.collector.stop()
        for c in {id(c): c for c in (self.lxd, self.metrics_lxd) if c is not None}.values():
            await c.aclose()
        if self.ceph is not None:
            await self.ceph.aclose()
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
    collector = Collector(api, live, history, settings, ceph)
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
    )
