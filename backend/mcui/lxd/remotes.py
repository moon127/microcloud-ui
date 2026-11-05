"""Remote image catalogues (simplestreams) for the create-instance image picker (IN-2).

LXD has no API to list a remote server's images, so the backend reads the
server's simplestreams index itself and keeps a compact summary for a while.
Only the named remotes below can be used: the browser picks a name, never a URL.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

import httpx

REMOTES: dict[str, str] = {
    "images": "https://images.lxd.canonical.com",
    "ubuntu": "https://cloud-images.ubuntu.com/releases",
}
CACHE_SECONDS = 6 * 3600

CONTAINER_FTYPES = frozenset({"squashfs", "root.tar.xz"})
VM_FTYPES = frozenset({"disk-kvm.img", "disk1.img", "uefi1.img"})

# LXD architecture names -> simplestreams names
ARCH = {
    "x86_64": "amd64",
    "aarch64": "arm64",
    "armv7l": "armhf",
    "ppc64le": "ppc64el",
    "s390x": "s390x",
}


@dataclass(frozen=True)
class RemoteImage:
    alias: str
    aliases: list[str]
    os: str
    release: str
    release_title: str
    variant: str
    arch: str
    container: bool
    vm: bool
    version: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_products(doc: dict[str, Any]) -> list[RemoteImage]:
    """Summarise a simplestreams products document, newest version of each product."""
    out: list[RemoteImage] = []
    for key, p in (doc.get("products") or {}).items():
        versions = p.get("versions") or {}
        if not versions:
            continue
        latest = max(versions)
        ftypes = {i.get("ftype") for i in (versions[latest].get("items") or {}).values()}
        has_meta = "lxd.tar.xz" in ftypes
        container = has_meta and bool(ftypes & CONTAINER_FTYPES)
        vm = has_meta and bool(ftypes & VM_FTYPES)
        if not (container or vm):
            continue
        raw = p.get("aliases") or ""
        aliases = [a.strip() for a in raw.split(",") if a.strip()]
        out.append(
            RemoteImage(
                alias=aliases[0] if aliases else key,
                aliases=aliases,
                os=str(p.get("os") or ""),
                release=str(p.get("release") or ""),
                release_title=str(p.get("release_title") or p.get("release") or ""),
                variant=str(p.get("variant") or "default"),
                arch=str(p.get("arch") or ""),
                container=container,
                vm=vm,
                version=latest,
            )
        )
    return sorted(out, key=lambda i: (i.os.lower(), i.release_title, i.variant, i.arch))


class RemoteCatalog:
    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = httpx.AsyncClient(timeout=60, transport=transport, follow_redirects=True)
        self._cache: dict[str, tuple[float, list[RemoteImage]]] = {}
        self._lock = asyncio.Lock()
        self.clock = clock

    async def images(self, remote: str) -> list[RemoteImage]:
        if remote not in REMOTES:
            raise KeyError(remote)
        async with self._lock:
            hit = self._cache.get(remote)
            if hit and self.clock() - hit[0] < CACHE_SECONDS:
                return hit[1]
            base = REMOTES[remote]
            index = (await self._get(f"{base}/streams/v1/index.json"))["index"]
            path = next(v["path"] for v in index.values() if v.get("datatype") == "image-downloads")
            items = parse_products(await self._get(f"{base}/{path}"))
            self._cache[remote] = (self.clock(), items)
            return items

    async def _get(self, url: str) -> dict[str, Any]:
        r = await self._http.get(url)
        r.raise_for_status()
        data: dict[str, Any] = r.json()
        return data

    async def aclose(self) -> None:
        await self._http.aclose()
