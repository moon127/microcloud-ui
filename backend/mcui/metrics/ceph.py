"""Scrape the ceph-mgr prometheus module (D11, D23): only the active mgr has data."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from mcui.config import CephSettings
from mcui.lxd.server import Member
from mcui.metrics.prom import Exposition, parse


def mgr_urls(settings: CephSettings, members: list[Member]) -> list[str]:
    """Configured endpoints, else every member's host on ``mgr_port``."""
    if settings.mgr_endpoints:
        return [u.rstrip("/") for u in settings.mgr_endpoints]
    urls = []
    for m in members:
        host = urlsplit(m.url).hostname or ""
        if ":" in host:
            host = f"[{host}]"
        urls.append(f"http://{host}:{settings.mgr_port}")
    return urls


@dataclass
class CephScrape:
    ok: bool
    url: str | None = None
    error: str | None = None
    exposition: Exposition = field(default_factory=Exposition)
    text: str = ""  # as served, for record-fixtures


class CephScraper:
    """Tries the last active mgr first, then all endpoints in parallel."""

    def __init__(self, settings: CephSettings) -> None:
        self.settings = settings
        self.active: str | None = None
        self._http = httpx.AsyncClient(timeout=settings.timeout_seconds)

    async def _one(self, url: str) -> CephScrape:
        try:
            r = await self._http.get(url + "/metrics")
        except httpx.HTTPError as e:
            return CephScrape(False, url, f"{type(e).__name__}: {e}".rstrip(": "))
        if r.status_code != 200:
            return CephScrape(False, url, f"HTTP {r.status_code}")
        exp = parse(r.text)
        if not exp.samples:
            return CephScrape(False, url, "standby mgr (no data)")
        return CephScrape(True, url, exposition=exp, text=r.text)

    async def scrape(self, members: list[Member]) -> CephScrape:
        urls = mgr_urls(self.settings, members)
        tried: list[CephScrape] = []
        if self.active is not None and self.active in urls:
            res = await self._one(self.active)
            if res.ok:
                return res
            tried.append(res)
        tried += await asyncio.gather(*(self._one(u) for u in urls if u != self.active))
        for res in tried:
            if res.ok:
                self.active = res.url
                return res
        self.active = None
        errors = "; ".join(f"{r.url}: {r.error}" for r in tried) or "no mgr endpoints"
        return CephScrape(False, error=f"no active ceph-mgr found ({errors})")

    async def aclose(self) -> None:
        await self._http.aclose()
