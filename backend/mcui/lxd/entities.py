"""Which project an LXD object belongs to, from its URL (D81).

LXD names objects by URL, with ``?project=`` for any project but ``default``:
``/1.0/instances/c1?project=web``, ``/1.0/projects/web``. Operations (no ``project``
field: LXD 6 puts the URL in ``metadata.entity_url``, 5.21 in ``resources``) and network
allocations (``used_by``) are only tied to a project this way, as seen on the test cloud.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

_PROJECT = re.compile(r"^/1\.0/projects/([^/?]+)$")


def url_project(url: str) -> str:
    """The project an LXD object URL belongs to."""
    parts = urlsplit(url)
    m = _PROJECT.match(parts.path)
    if m:
        return unquote(m.group(1))
    return parse_qs(parts.query).get("project", ["default"])[0]


def operation_project(op: dict[str, Any]) -> str | None:
    """The project an operation works in; ``None`` when it names no object (cluster-wide,
    e.g. "Synchronizing operations")."""
    entity = (op.get("metadata") or {}).get("entity_url")
    urls = [entity] if isinstance(entity, str) else []
    for group in (op.get("resources") or {}).values():
        urls += [u for u in group or [] if isinstance(u, str)]
    return url_project(urls[0]) if urls else None
