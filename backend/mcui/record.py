"""``mcui record-fixtures``: capture scrubbed LXD responses for tests (D26)."""

from __future__ import annotations

import ipaddress
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcui.config import Settings
from mcui.lxd.client import LXDClient, LXDError
from mcui.lxd.server import members, server_info
from mcui.metrics.scrape import Scraper

# PEM blocks, raw (real newlines) or inside JSON strings (escaped "\\n")
_PEM = re.compile(r"-----BEGIN ([A-Z ]+)-----(\\n|\n).*?-----END \1-----", re.S)
_MAC = re.compile(r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f:])")
_FPR = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{64}(?![0-9a-fA-F])")
_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_IPV6 = re.compile(r"(?<![\w:.])[0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{0,4}){2,7}(?![\w:])")
_KEEP = {"127.0.0.1", "0.0.0.0", "::1", "::"}  # noqa: S104 - literal kept, not bound


class Scrubber:
    """Replace IPs, MACs, fingerprints and PEM blocks with stable placeholders."""

    def __init__(self) -> None:
        self.map: dict[str, str] = {}
        self._n: dict[str, int] = {}

    def _sub(self, kind: str, original: str, make: Callable[[int], str]) -> str:
        key = f"{kind}:{original.lower()}"
        if key not in self.map:
            self._n[kind] = self._n.get(kind, 0) + 1
            self.map[key] = make(self._n[kind])
        return self.map[key]

    def _ipv4(self, m: re.Match[str]) -> str:
        s = m.group(0)
        try:
            ipaddress.IPv4Address(s)
        except ValueError:
            return s
        if s in _KEEP:
            return s
        return self._sub("ip4", s, lambda n: f"192.0.2.{n % 254 + 1}")

    def _ipv6(self, m: re.Match[str]) -> str:
        s = m.group(0)
        try:
            ipaddress.IPv6Address(s)
        except ValueError:
            return s
        if s in _KEEP:
            return s
        return self._sub("ip6", s, lambda n: f"2001:db8::{n:x}")

    def scrub(self, text: str) -> str:
        text = _PEM.sub(
            lambda m: f"-----BEGIN {m[1]}-----{m[2]}SCRUBBED{m[2]}-----END {m[1]}-----", text
        )
        text = _MAC.sub(
            lambda m: self._sub(
                "mac", m.group(0), lambda n: f"00:16:3e:00:{n >> 8:02x}:{n & 255:02x}"
            ),
            text,
        )
        text = _FPR.sub(lambda m: self._sub("fpr", m.group(0), lambda n: f"{n:064x}"), text)
        text = _IPV4.sub(self._ipv4, text)
        return _IPV6.sub(self._ipv6, text)


REDACT_KEYS = frozenset({"serial", "uuid", "wwn", "machine_id"})


def redact(value: Any) -> Any:
    """Blank hardware identifiers (serials, UUIDs, WWNs) anywhere in a JSON document."""
    if isinstance(value, dict):
        return {k: "SCRUBBED" if k in REDACT_KEYS and v else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def _fname(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", path.strip("/")) or "root"


async def _pool_paths(api: LXDClient, members: list[str | None]) -> list[str]:
    """Each pool's per-member view, its capacity and its volumes (local pools, D54/D55)."""
    try:
        names = [str(u).rsplit("/", 1)[-1] for u in await api.get("/1.0/storage-pools") or []]
    except (LXDError, ValueError):
        return []
    out = []
    for name in names:
        for m in members:
            t = f"?target={m}" if m else ""
            out += [f"/1.0/storage-pools/{name}{t}", f"/1.0/storage-pools/{name}/resources{t}"]
        out.append(f"/1.0/storage-pools/{name}/volumes?recursion=1&all-projects=true")
    return out


async def record(
    settings: Settings,
    api: LXDClient,
    metrics: LXDClient,
    out_dir: Path,
    log: Callable[[str], None],
) -> int:
    """Fetch, scrub and write fixtures. Returns the number of failures."""
    scrub = Scrubber()
    failures = 0
    out_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240 - CLI only

    def write(name: str, text: str) -> None:
        (out_dir / name).write_text(scrub.scrub(text))
        log(f"  wrote {name}")

    info = await server_info(api)
    ms = await members(api, info)
    paths = [
        "/1.0",
        "/1.0/cluster/members?recursion=1",
        "/1.0/instances?recursion=2&all-projects=true",
        "/1.0/storage-pools?recursion=1",
        "/1.0/networks?recursion=1",
        "/1.0/warnings?recursion=1",
    ]
    paths += [f"/1.0/cluster/members/{m.name}/state" for m in ms if info.clustered]
    paths += [f"/1.0/resources?target={m.name}" for m in ms if info.clustered]
    paths += await _pool_paths(api, [m.name for m in ms] if info.clustered else [None])
    for p in paths:
        path, _, query = p.partition("?")
        params = dict(kv.split("=", 1) for kv in query.split("&")) if query else None
        try:
            r = await api.raw("GET", path, params=params)
        except LXDError as e:
            log(f"  FAIL {p}: {e}")
            failures += 1
            continue
        try:
            text = json.dumps(redact(r.json()), indent=2, sort_keys=True) + "\n"
        except ValueError:
            text = r.text
        write(_fname(p) + ".json", text)

    for s in await Scraper(metrics, settings.metrics).scrape_all(ms):
        if s.ok:
            write(f"metrics_{_fname(s.member)}.txt", s.raw)
        else:
            log(f"  FAIL metrics {s.member}: {s.error}")
            failures += 1
    log(f"Scrubbed {len(scrub.map)} identifiers. Review {out_dir} before committing.")
    return failures
