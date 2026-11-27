"""Reading an exporter's page with a size cap (D89).

node_exporter and the Ceph mgr are other services on the network; the whole body used to
be read into memory, so a broken or hostile exporter could exhaust the server's memory.
A real page is well under a megabyte (node_exporter ~100 KiB, Ceph mgr ~1 MiB here).
"""

from __future__ import annotations

from typing import Any

import httpx

MAX_BODY = 32 * 2**20


class BodyTooLarge(httpx.HTTPError):
    """The page was bigger than ``MAX_BODY``."""


async def get_text(
    http: httpx.AsyncClient, url: str, limit: int = MAX_BODY, **kw: Any
) -> tuple[int, str]:
    """GET ``url``: the status and the body as text, refusing bodies over ``limit``."""
    async with http.stream("GET", url, **kw) as r:
        size = int(r.headers.get("content-length") or 0)
        if size > limit:
            raise BodyTooLarge(f"{size} bytes, over the {limit}-byte limit")
        chunks, total = [], 0
        async for chunk in r.aiter_bytes():
            total += len(chunk)
            if total > limit:
                raise BodyTooLarge(f"over the {limit}-byte limit")
            chunks.append(chunk)
        return r.status_code, b"".join(chunks).decode(r.encoding or "utf-8", "replace")
