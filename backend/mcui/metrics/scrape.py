"""Scrape ``/1.0/metrics`` from every cluster member (D7, D22, CF-4, MO-1)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from mcui.config import MetricsSettings
from mcui.lxd.client import LXDClient, LXDError, LXDUnavailable
from mcui.lxd.server import Member
from mcui.metrics.prom import Exposition, parse


def with_port(url: str, port: int) -> str:
    """``https://host:any`` -> ``https://host:port`` (IPv6-safe)."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    return f"{parts.scheme or 'https'}://{host}:{port}"


def metrics_url(member: Member, settings: MetricsSettings) -> str:
    override = settings.member_overrides.get(member.name)
    if override:
        return override.rstrip("/")
    return with_port(member.url, settings.port)


@dataclass
class MemberScrape:
    member: str
    url: str
    ok: bool
    at: float
    error: str | None = None
    fell_back: bool = False
    exposition: Exposition = field(default_factory=Exposition)
    raw: str = field(default="", repr=False)

    @property
    def port(self) -> int | None:
        return urlsplit(self.url).port

    def status(self) -> dict[str, object]:
        return {
            "member": self.member,
            "url": self.url,
            "port": self.port,
            "ok": self.ok,
            "error": self.error,
            "fell_back": self.fell_back,
            "samples": len(self.exposition.samples),
        }


class Scraper:
    def __init__(
        self,
        client: LXDClient,
        settings: MetricsSettings,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.client = client
        self.settings = settings
        self.clock = clock

    async def scrape_member(self, member: Member) -> MemberScrape:
        url = metrics_url(member, self.settings)
        try:
            return await self._scrape(member.name, url)
        except LXDUnavailable as e:
            api_url = with_port(member.url, urlsplit(member.url).port or 8443)
            if self.settings.fallback_to_api_port and api_url != url:
                try:
                    res = await self._scrape(member.name, api_url)
                except LXDError as e2:
                    return MemberScrape(member.name, api_url, False, self.clock(), str(e2))
                res.fell_back = True
                return res
            return MemberScrape(member.name, url, False, self.clock(), str(e))

    async def _scrape(self, name: str, url: str) -> MemberScrape:
        r = await self.client.raw("GET", "/1.0/metrics", base=url)
        now = self.clock()
        if r.status_code == 403:
            return MemberScrape(name, url, False, now, "metrics certificate not trusted (HTTP 403)")
        if r.status_code != 200:
            return MemberScrape(name, url, False, now, f"HTTP {r.status_code}")
        return MemberScrape(name, url, True, now, exposition=parse(r.text), raw=r.text)

    async def scrape_all(self, members: list[Member]) -> list[MemberScrape]:
        return list(await asyncio.gather(*(self.scrape_member(m) for m in members)))
