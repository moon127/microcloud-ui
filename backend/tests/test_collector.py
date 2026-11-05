import asyncio

import httpx
import pytest
import respx

from mcui.config import CephSettings
from mcui.metrics.ceph import CephScraper
from mcui.metrics.collector import Collector
from mcui.metrics.live import LiveMetrics
from mcui.metrics.scrape import Scraper
from mcui.metrics.store import RAW, MetricStore, SQLiteMetricStore
from tests.conftest import EP1, fixture_json, fixture_text, lxd_err, lxd_ok

NOW = 1_700_000_000


@pytest.fixture
async def store():
    s = MetricStore(SQLiteMetricStore(":memory:", clock=lambda: NOW))
    yield s
    await s.aclose()


@pytest.fixture
def collector(lxd, store, settings):
    live = LiveMetrics(lxd, Scraper(lxd, settings.metrics, clock=lambda: NOW))
    return Collector(lxd, live, store, settings, CephScraper(CephSettings()), clock=lambda: NOW)


def mock_cluster(m2_metrics=None):
    respx.get(f"{EP1}/1.0").mock(return_value=lxd_ok(fixture_json("server_clustered.json")))
    respx.get(f"{EP1}/1.0/cluster/members", params={"recursion": "1"}).mock(
        return_value=lxd_ok(fixture_json("cluster_members.json"))
    )
    respx.get("https://192.0.2.1:8443/1.0/metrics").mock(
        return_value=httpx.Response(200, text=fixture_text("metrics_m1_t0.txt"))
    )
    respx.get("https://192.0.2.2:8443/1.0/metrics").mock(
        return_value=m2_metrics or httpx.Response(200, text=fixture_text("metrics_m2_t0.txt"))
    )


def series_count(store, name):
    return len(store.backend.find_series(name))


@respx.mock
async def test_collect_lxd(collector, store):
    mock_cluster()
    n = await collector.collect_lxd()
    assert n > 20
    busy = store.backend.find_series("mcui_instance_cpu_busy_seconds_total")
    assert {lab["name"] for _, lab, _ in busy} == {"c1", "v1", "c2"}
    r = store.backend.query("mcui_instance_cpus", {"name": "c1"}, NOW - 60, NOW + 60, res=RAW)
    assert r.series[0].points == [(NOW, 2.0)]


@respx.mock
async def test_collect_lxd_reports_failed_members(collector, store):
    mock_cluster(m2_metrics=httpx.Response(403))
    with pytest.raises(RuntimeError, match="m2: metrics certificate not trusted"):
        await collector.collect_lxd()
    assert series_count(store, "mcui_instance_cpu_busy_seconds_total") == 2  # m1 still stored


@respx.mock
async def test_collect_ceph(collector, store):
    mock_cluster()
    await collector.collect_lxd()
    respx.get("http://192.0.2.1:9283/metrics").mock(
        return_value=httpx.Response(
            200, text='# TYPE ceph_pool_rd counter\nceph_pool_rd{pool_id="1"} 5\n'
        )
    )
    respx.get("http://192.0.2.2:9283/metrics").mock(return_value=httpx.Response(200, text=""))
    assert await collector.collect_ceph() == 1
    assert store.backend.find_series("ceph_pool_rd")[0][2] == "counter"
    respx.get("http://192.0.2.1:9283/metrics").mock(return_value=httpx.Response(200, text=""))
    with pytest.raises(RuntimeError, match="no active ceph-mgr"):
        await collector.collect_ceph()
    collector.ceph = None
    assert await collector.collect_ceph() == 0


@respx.mock
async def test_collect_state(collector, store):
    mock_cluster()  # members come from LXD when no scrape has run yet
    state = {"sysinfo": {"load_averages": [1, 2, 3], "total_ram": 100}, "storage_pools": {}}
    respx.get(f"{EP1}/1.0/cluster/members/m1/state").mock(return_value=lxd_ok(state))
    respx.get(f"{EP1}/1.0/cluster/members/m2/state").mock(return_value=lxd_err("not found", 404))
    nets = [{"name": "bond0", "type": "bond"}, {"name": "ovn1", "type": "ovn"}]
    respx.get(f"{EP1}/1.0/networks").mock(return_value=lxd_ok(nets))
    respx.get(f"{EP1}/1.0/networks/bond0/state").mock(
        return_value=lxd_ok({"counters": {"bytes_received": 5, "bytes_sent": 6}})
    )
    assert await collector.collect_state() == 4 + 2 + 2
    assert series_count(store, "mcui_member_load5") == 1  # m2 404 = standalone-style: skipped
    assert series_count(store, "mcui_host_network_receive_bytes_total") == 2

    respx.get(f"{EP1}/1.0/cluster/members/m2/state").mock(return_value=lxd_err("boom", 500))
    with pytest.raises(RuntimeError, match="m2: boom"):
        await collector.collect_state()


async def test_guard_records_status(collector):
    async def ok():
        return 7

    async def bad():
        raise RuntimeError

    await collector._guard("lxd", ok)
    st = collector.status["lxd"]
    assert st.samples == 7 and st.last_ok == NOW and st.last_error is None
    await collector._guard("lxd", bad)
    assert st.last_error == "RuntimeError" and st.last_ok == NOW
    assert collector.status_dict()["lxd"]["samples"] == 7


async def test_compact_job(collector, store):
    assert await collector.compact() == 0


async def test_loops_start_and_stop(collector, monkeypatch):
    rounds = []

    async def fake(name, job):
        rounds.append(name)

    async def scrape_round():
        rounds.append("scrape")

    monkeypatch.setattr(collector, "_guard", fake)
    monkeypatch.setattr(collector, "_scrape_round", scrape_round)
    collector.start()
    assert collector.running and collector.live.passive
    await asyncio.sleep(0.01)
    await collector.stop()
    assert not collector.running and not collector.live.passive
    assert sorted(rounds) == ["compact", "scrape", "state"]


@respx.mock
async def test_scrape_round_runs_ceph_only_when_enabled(collector):
    calls = []

    async def lxd():
        calls.append("lxd")
        return 0

    async def ceph():
        calls.append("ceph")
        return 0

    collector.collect_lxd = lxd
    collector.collect_ceph = ceph
    await collector._scrape_round()
    collector.ceph = None
    await collector._scrape_round()
    assert calls == ["lxd", "ceph", "lxd"]
