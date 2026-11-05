import time

import pytest

from mcui.config import Settings
from mcui.main import create_app
from mcui.metrics.store import MetricStore, Sample, SQLiteMetricStore
from mcui.services import build_services
from tests.conftest import fast_store
from tests.test_api import client, login, lxd_pair

NOW = int(time.time())
# both readings (T - 15 and T) fall in the same, already finished minute
T = NOW - NOW % 60 - 30


def inst(
    name,
    value,
    project="default",
    member="m1",
    metric="mcui_instance_cpu_busy_seconds_total",
    **extra,
):
    kind = "counter" if metric.endswith("_total") else "gauge"
    labels = {"member": member, "project": project, "name": name, **extra}
    return Sample(metric, labels, value, kind)


@pytest.fixture
async def services(tmp_path):
    store = fast_store()
    store.add_user("viewer", "viewerpw", "viewer")
    settings = Settings(server={"data_dir": str(tmp_path), "secure_cookies": False})
    ms = MetricStore(SQLiteMetricStore(":memory:"))
    svc = build_services(settings, store=store, clients=lxd_pair(), metrics_store=ms)
    # two scrapes 15 s apart: busy (CPU s), cpus, memory, net and disk counters
    for i, t in enumerate((T - 15, T)):
        await ms.write(
            t,
            [
                inst("busy", 100 + 15 * i),  # 1 CPU-second/s
                inst("busy", 2, metric="mcui_instance_cpus"),
                inst("idle", 10 + 0.15 * i),  # 0.01/s
                inst("idle", 1, metric="mcui_instance_cpus"),
                inst("nocpus", 5 + 15 * i),  # no cpus series: excluded from cpu top
                inst("busy", 1e9, metric="mcui_instance_memory_used_bytes"),
                inst(
                    "idle",
                    2e9,
                    metric="mcui_instance_memory_used_bytes",
                    project="web",
                    member="m2",
                ),
                inst("busy", 1500 * i, metric="lxd_network_receive_bytes_total", device="eth0"),
                inst("busy", 3000 * i, metric="lxd_network_transmit_bytes_total", device="eth0"),
                inst("idle", 150 * i, metric="lxd_disk_read_bytes_total", device="sda"),
            ],
        )
    return svc


@pytest.fixture
def app(services):
    return create_app(services=services, static_dir=None)


async def test_requires_login(app):
    async with client(app) as c:
        assert (await c.get("/api/metrics/names")).status_code == 401


async def test_names_series_query(app):
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        names = {n["name"]: n for n in (await c.get("/api/metrics/names")).json()}
        assert names["mcui_instance_cpus"]["series"] == 2
        assert names["lxd_disk_read_bytes_total"]["kind"] == "counter"

        r = await c.get(
            "/api/metrics/series", params={"name": "mcui_instance_cpus", "match": "name=busy"}
        )
        assert r.json() == [{"member": "m1", "name": "busy", "project": "default"}]

        r = await c.get(
            "/api/metrics/query",
            params={
                "name": "mcui_instance_cpu_busy_seconds_total",
                "match": "name=busy",
                "fn": "rate",
            },
        )
        body = r.json()
        assert (
            body["resolution"] == 0
            and body["kind"] == "counter"
            and body["end"] - body["start"] == 3600
        )
        assert body["series"][0]["points"] == [[T, 1.0]]

        r = await c.get(
            "/api/metrics/query",
            params=[
                ("name", "mcui_instance_cpus"),
                ("by", "project"),
                ("start", T - 60),
                ("end", T + 1),
                ("res", 60),
            ],
        )
        assert r.json()["series"] == [
            {"labels": {"project": "default"}, "points": [[T - T % 60, 3.0]]}
        ]


@pytest.mark.parametrize(
    "params,detail",
    [
        ({"match": "nolabel"}, "label=value"),
        ({"res": 5}, "res must be"),
        ({"start": 10, "end": 5}, "invalid time range"),
        ({"start": 0}, "invalid time range"),
    ],
)
async def test_query_bad_params(app, params, detail):
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        r = await c.get("/api/metrics/query", params={"name": "x", **params})
        assert r.status_code == 400 and detail in r.json()["detail"]


async def test_top(app):
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        cpu = (await c.get("/api/metrics/top", params={"metric": "cpu"})).json()
        assert cpu["unit"] == "percent"
        assert [(r["name"], round(r["value"], 3)) for r in cpu["rows"]] == [
            ("busy", 50.0),
            ("idle", 1.0),
        ]
        mem = (await c.get("/api/metrics/top", params={"metric": "memory", "limit": 1})).json()
        assert [(r["project"], r["name"]) for r in mem["rows"]] == [("web", "idle")]
        mem = (await c.get("/api/metrics/top", params={"metric": "memory", "member": "m1"})).json()
        assert [r["name"] for r in mem["rows"]] == ["busy"]
        net = (await c.get("/api/metrics/top", params={"metric": "net"})).json()
        assert net["rows"] == [{"project": "default", "name": "busy", "value": 300.0}]
        disk = (
            await c.get("/api/metrics/top", params={"metric": "disk", "project": "default"})
        ).json()
        assert disk["rows"] == [{"project": "default", "name": "idle", "value": 10.0}]
        assert (await c.get("/api/metrics/top", params={"metric": "bogus"})).status_code == 422


async def test_status(app, services):
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        body = (await c.get("/api/metrics/status")).json()
    assert body["collecting"] is False
    assert set(body["jobs"]) == {"lxd", "ceph", "state", "compact"}
    assert body["store"]["series"] > 0


async def test_history_disabled_without_lxd(tmp_path):
    store = fast_store()
    store.add_user("v", "pw")
    svc = build_services(
        Settings(server={"data_dir": str(tmp_path), "secure_cookies": False}), store=store
    )
    async with client(create_app(services=svc, static_dir=None)) as c:
        await login(c, "v", "pw")
        r = await c.get("/api/metrics/names")
    assert r.status_code == 503 and r.json()["detail"] == "metric history is not enabled"


async def test_lifespan_starts_and_stops_collector(services):
    app = create_app(services=services, static_dir=None)
    async with app.router.lifespan_context(app):
        assert services.collector.running
    assert not services.collector.running


async def test_collect_disabled(tmp_path):
    settings = Settings(
        server={"data_dir": str(tmp_path)}, metrics={"collect": False}, ceph={"enabled": False}
    )
    svc = build_services(settings, store=fast_store(), clients=lxd_pair())
    assert svc.ceph is None and svc.metrics is not None
    await svc.start()
    assert not svc.collector.running
    await svc.aclose()
    assert (tmp_path / "metrics.db").is_file()


async def test_network_traffic(app, services):
    from mcui.lxd.instances import InstanceInfo

    async def fake_get():
        return [
            InstanceInfo("default", "busy", "container", "Running", "m1", "", {"eth0": "ovn1"}),
        ]

    services.instances.get = fake_get
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        body = (await c.get("/api/metrics/network-traffic", params={"range": 600})).json()
        assert body["resolution"] == 0
        assert body["rx"] == [{"labels": {"network": "ovn1"}, "points": [[T, 100.0]]}]
        assert body["tx"] == [{"labels": {"network": "ovn1"}, "points": [[T, 200.0]]}]
        r = await c.get("/api/metrics/network-traffic", params={"res": 7})
        assert r.status_code == 400
        body = (await c.get("/api/metrics/network-traffic", params={"res": 60})).json()
        assert body["rx"][0]["points"] == [[T - T % 60, 1500 / 60]]  # one 1500 B increase
