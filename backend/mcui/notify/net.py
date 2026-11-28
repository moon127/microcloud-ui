"""Where a project's channel may send (D94).

A project admin sets the URL, and the app sends from inside the cluster's network, so a
project channel may only reach public addresses: not the LXD API, node_exporter, the Ceph
mgr, a cloud metadata service or anything else on the hosts' networks. Checked when the
channel is saved and before every send (a name can be pointed elsewhere later), and the
send then connects to the very address that was checked (``PinnedTransport``), so a name
that answers differently the second time (DNS rebinding) can't slip past, and no proxy
from the environment gets to resolve it again (D94 review). Cluster channels may go
anywhere: a self-hosted ntfy or chat server on the LAN is their admin's choice.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

import httpx

Address = ipaddress.IPv4Address | ipaddress.IPv6Address
# IPv6 ranges that carry an IPv4 address in their last 32 bits: NAT64, IPv4-compatible,
# IPv4-translated (D94 review)
EMBEDDED_V4 = tuple(ipaddress.ip_network(n) for n in ("64:ff9b::/96", "::/96", "::ffff:0:0:0/96"))
REFUSED = tuple(
    ipaddress.ip_network(n)
    for n in ("fec0::/10", "192.88.99.0/24")  # site-local; the old 6to4 relay anycast
)
RESOLVE_SECONDS = 5.0


class NotPublic(ValueError):
    """The URL leads to an address a project's channel may not reach."""


def is_public(addr: Address) -> bool:
    """A globally routed unicast address, looking through IPv4 carried in IPv6 (mapped,
    6to4, NAT64 and older forms), which could otherwise reach a private IPv4 address."""
    if addr.is_multicast or any(addr in n for n in REFUSED if n.version == addr.version):
        return False
    if isinstance(addr, ipaddress.IPv6Address):
        inner = addr.ipv4_mapped or addr.sixtofour
        if inner is None and any(addr in n for n in EMBEDDED_V4):
            inner = ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
        if inner is not None:
            return is_public(inner)
    return addr.is_global


async def resolve(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await asyncio.wait_for(
        loop.getaddrinfo(host, port, type=socket.SOCK_STREAM), RESOLVE_SECONDS
    )
    return [str(i[4][0]) for i in infos]


async def check_public(urls: list[str]) -> dict[str, str]:
    """Each URL's host, resolved and checked: ``{host: address to connect to}``. Every
    address the name has must be public. The message doesn't say what a name resolved
    to, or whether it resolved, so it can't be used to look into the cluster's DNS."""
    pins: dict[str, str] = {}
    for url in urls:
        host = urlsplit(url).hostname or ""
        why = f"{host} isn't a public address: a project's channels may only send to public servers"
        try:
            addrs = [a.split("%")[0] for a in await resolve(host, urlsplit(url).port or 443)]
            ok = bool(addrs) and all(is_public(ipaddress.ip_address(a)) for a in addrs)
        except (OSError, ValueError, UnicodeError, TimeoutError) as e:
            raise NotPublic(why) from e
        if not ok:
            raise NotPublic(why)
        pins[host] = addrs[0]
    return pins


class PinnedTransport(httpx.AsyncBaseTransport):
    """Connects to the checked address of each pinned host, while the request keeps the
    name for its Host header and for TLS (SNI and the certificate check). A host that
    wasn't checked is refused."""

    def __init__(self, pins: dict[str, str], inner: httpx.AsyncBaseTransport) -> None:
        self.pins, self.inner = pins, inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        addr = self.pins.get(host)
        if addr is None:
            raise httpx.ConnectError(f"{host} wasn't checked", request=request)
        request.extensions = {**request.extensions, "sni_hostname": host}
        request.url = request.url.copy_with(host=addr)
        return await self.inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self.inner.aclose()
