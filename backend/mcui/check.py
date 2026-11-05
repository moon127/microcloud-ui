"""``mcui check``: validate connectivity, trust and API extensions (CF-2, CF-3, CF-4)."""

from __future__ import annotations

from collections.abc import Callable

from mcui.config import Settings
from mcui.lxd.client import LXDClient, LXDError
from mcui.lxd.server import FEATURES, ServerInfo, members
from mcui.metrics.scrape import Scraper

Out = Callable[[str], None]


async def run_check(settings: Settings, api: LXDClient, metrics: LXDClient, out: Out) -> bool:
    """Print a report through ``out``; return True when everything needed works."""
    ok = True
    info: ServerInfo | None = None
    out("LXD endpoints:")
    for ep in api.endpoints:
        try:
            md = await api.query("GET", "/1.0", base=ep)
        except LXDError as e:
            out(f"  FAIL {ep}: {e}")
            ok = False
            continue
        this = ServerInfo.from_metadata(md)
        if not this.trusted:
            out(f"  FAIL {ep}: management certificate is not trusted (auth={this.auth!r})")
            ok = False
            continue
        out(
            f"  ok   {ep}: LXD {this.version}, "
            f"{'clustered' if this.clustered else 'standalone'}, "
            f"server {this.server_name}, auth {this.auth_method or this.auth}"
        )
        info = info or this
    if info is None:
        out("No trusted LXD endpoint; cannot continue.")
        return False

    try:
        ms = await members(api, info)
    except LXDError as e:
        out(f"Cluster members: FAIL {e}")
        return False
    out(f"Cluster members ({len(ms)}):")
    for m in ms:
        flag = "ok  " if m.status == "Online" else "WARN"
        out(f"  {flag} {m.name}: {m.status} {m.url}")

    port = settings.metrics.port
    using = "metrics cert" if settings.metrics.cert else "management cert"
    out(f"Metrics (port {port}, {using}):")
    for s in await Scraper(metrics, settings.metrics).scrape_all(ms):
        if s.ok:
            note = " via fallback to API port" if s.fell_back else ""
            out(f"  ok   {s.member}: {len(s.exposition.samples)} samples ({s.port}){note}")
        else:
            out(f"  FAIL {s.member}: {s.url}: {s.error}")
            ok = False

    feats = info.features()
    on = sorted(k for k, v in feats.items() if v)
    off = sorted(k for k, v in feats.items() if not v)
    out(f"Features: {', '.join(on) or 'none'}")
    if off:
        out("Unavailable (hidden in the UI): " + ", ".join(off))
    if not feats["metrics"]:
        out("FAIL: this LXD has no metrics API extension")
        ok = False
    out("Result: " + ("OK" if ok else "FAILED"))
    return ok


__all__ = ["FEATURES", "run_check"]
