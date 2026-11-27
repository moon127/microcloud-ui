"""Request bodies streamed to LXD, with a size cap, and download headers (D89).

Uploads go to LXD as they arrive, so the app never holds them, but nothing limited their
size: a project admin's image upload lands on a member's root filesystem, outside project
limits, and could fill it. ``[server] max_upload_bytes`` caps every upload; an announced
size over it is refused at once (413), and a body that turns out bigger is cut off.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from urllib.parse import quote

from fastapi import HTTPException, Request


def too_large(limit: int) -> HTTPException:
    return HTTPException(
        status_code=413, detail=f"the upload is larger than the limit of {limit} bytes"
    )


def capped(request: Request) -> AsyncIterator[bytes]:
    """The body as it arrives, up to ``[server] max_upload_bytes`` (0: no limit)."""
    limit: int = request.app.state.services.settings.server.max_upload_bytes
    declared = request.headers.get("content-length", "")
    if limit and not declared.isdigit():
        # unannounced, a body over the limit would be cut off only after LXD started
        # writing it, leaving a truncated file or volume (D90); browsers always send it
        raise HTTPException(status_code=411, detail="send the upload's size (Content-Length)")
    if limit and int(declared) > limit:
        raise too_large(limit)

    async def body() -> AsyncIterator[bytes]:
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if limit and total > limit:
                raise too_large(limit)
            yield chunk

    return body()


def attachment(name: str) -> str:
    """A Content-Disposition for ``name``: an ASCII fallback plus the UTF-8 name (RFC 6266).
    A name with non-ASCII characters or a newline used to fail the response with a 500."""
    ascii_name = re.sub(r"[^A-Za-z0-9._-]", "_", name) or "download"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name, safe='')}"
