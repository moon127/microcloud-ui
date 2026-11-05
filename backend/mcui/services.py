"""Long-lived objects shared by the API: settings, store, LXD clients, live metrics."""

from __future__ import annotations

from dataclasses import dataclass

from mcui.auth.store import Store
from mcui.config import Settings
from mcui.lxd.client import LXDClient
from mcui.metrics.live import LiveMetrics
from mcui.metrics.scrape import Scraper


@dataclass
class Services:
    settings: Settings
    store: Store
    lxd: LXDClient | None = None
    metrics_lxd: LXDClient | None = None
    live: LiveMetrics | None = None

    async def aclose(self) -> None:
        for c in {id(c): c for c in (self.lxd, self.metrics_lxd) if c is not None}.values():
            await c.aclose()
        self.store.close()


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
) -> Services:
    store = store or Store.open(settings.server.data_dir)
    clients = clients or lxd_clients(settings)
    if clients is None:
        return Services(settings, store)
    api, metrics = clients
    live = LiveMetrics(api, Scraper(metrics, settings.metrics))
    return Services(settings, store, api, metrics, live)
