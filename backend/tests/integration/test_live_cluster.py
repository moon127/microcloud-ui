"""Opt-in checks against a real MicroCloud (D26). Run on a machine that can reach it:

MCUI_IT_CONFIG=~/.config/mcui/config.toml pytest tests/integration -q --no-cov
"""

import os

import pytest

from mcui.config import load_settings
from mcui.lxd.server import members, server_info
from mcui.metrics.live import LiveMetrics
from mcui.metrics.scrape import Scraper
from mcui.services import lxd_clients

CONFIG = os.environ.get("MCUI_IT_CONFIG")
pytestmark = pytest.mark.skipif(not CONFIG, reason="set MCUI_IT_CONFIG to run live tests")


@pytest.fixture
async def clients():
    settings = load_settings(CONFIG)
    pair = lxd_clients(settings)
    assert pair is not None, "config has no [lxd] section"
    yield settings, *pair
    for c in pair:
        await c.aclose()


async def test_trusted_and_members(clients):
    _, api, _ = clients
    info = await server_info(api)
    assert info.trusted, f"management cert not trusted: auth={info.auth}"
    assert info.features()["metrics"]
    ms = await members(api, info)
    assert ms and all(m.url.startswith("https://") for m in ms)


async def test_live_metrics_every_member(clients):
    settings, api, metrics = clients
    live = LiveMetrics(api, Scraper(metrics, settings.metrics))
    snap = await live.snapshot()
    bad = [m["name"] for m in snap["members"] if not m["scrape"]["ok"]]
    assert not bad, f"metrics scrape failed on {bad}"
    assert all(m["daemon"].get("uptime_seconds", 0) > 0 for m in snap["members"])
