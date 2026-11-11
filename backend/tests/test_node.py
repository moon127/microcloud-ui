from pathlib import Path

import httpx
import pytest
import respx

from mcui.config import NodeExporterSettings, Settings
from mcui.lxd.server import Member
from mcui.metrics.collector import Collector
from mcui.metrics.live import LiveMetrics
from mcui.metrics.node import NodeScraper, _ssl_context, host_disk, host_now, node_url
from mcui.metrics.prom import parse
from mcui.metrics.scrape import MemberScrape, Scraper
from mcui.metrics.select import node_samples
from mcui.metrics.store import RAW, MetricStore, SQLiteMetricStore

FIXTURE = Path(__file__).parent / "fixtures" / "node" / "synthetic.txt"
NOW = 1_700_000_000
M1 = Member("m1", "https://192.0.2.1:8443", "Online")
M2 = Member("m2", "https://192.0.2.2:8443", "Online")
U1 = "https://192.0.2.1:9100/metrics"
U2 = "https://192.0.2.2:9100/metrics"


def later(text: str, dt: float = 10) -> str:
    """The fixture ``dt`` seconds on: each CPU 50% busy (20% user, 10% system,
    10% iowait, 10% steal), nvme0n1 25% busy, sda 50%, pressure 5% cpu / 10% io."""
    add = {
        'node_cpu_seconds_total{cpu="0",mode="idle"}': 5,
        'node_cpu_seconds_total{cpu="1",mode="idle"}': 5,
        'node_cpu_seconds_total{cpu="0",mode="user"}': 2,
        'node_cpu_seconds_total{cpu="1",mode="user"}': 2,
        'node_cpu_seconds_total{cpu="0",mode="system"}': 1,
        'node_cpu_seconds_total{cpu="1",mode="system"}': 1,
        'node_cpu_seconds_total{cpu="0",mode="iowait"}': 1,
        'node_cpu_seconds_total{cpu="1",mode="iowait"}': 1,
        'node_cpu_seconds_total{cpu="0",mode="steal"}': 1,
        'node_cpu_seconds_total{cpu="1",mode="steal"}': 1,
        'node_disk_io_time_seconds_total{device="nvme0n1"}': 2.5,
        'node_disk_io_time_seconds_total{device="sda"}': 5,
        'node_disk_io_time_seconds_total{device="dm-0"}': 10,
        'node_disk_read_bytes_total{device="nvme0n1"}': 1000,
        'node_disk_read_bytes_total{device="sda"}': 1000,
        'node_disk_read_bytes_total{device="rbd0"}': 1e9,
        'node_disk_written_bytes_total{device="sda"}': 5000,
        "node_pressure_cpu_waiting_seconds_total": 0.5,
        "node_pressure_io_waiting_seconds_total": 1,
    }
    out = []
    for line in text.splitlines():
        key, _, value = line.rpartition(" ")
        if key in add:
            line = f"{key} {float(value) + add[key]}"
        out.append(line)
    return "\n".join(out) + "\n"


@pytest.fixture
def clock():
    t = [float(NOW)]
    return t


@pytest.fixture
async def scraper(clock):
    s = NodeScraper(NodeExporterSettings(), clock=lambda: clock[0])
    yield s
    await s.aclose()


def test_urls():
    s = NodeExporterSettings()
    assert node_url(M1, s) == "https://192.0.2.1:9100"
    assert (
        node_url(Member("v6", "https://[2001:db8::1]:8443", ""), s) == "https://[2001:db8::1]:9100"
    )
    s = NodeExporterSettings(scheme="http", port=9101, member_overrides={"m2": "http://n2:1/"})
    assert node_url(M1, s) == "http://192.0.2.1:9101"
    assert node_url(M2, s) == "http://n2:1"


def test_host_disks():
    assert host_disk("nvme0n1") and host_disk("sda") and host_disk("vdb")
    for d in ("nvme0n1p1", "sda2", "dm-0", "loop3", "rbd0", "nbd1", "zd16", "sr0", "md127"):
        assert not host_disk(d), d


def test_node_samples_keep_what_the_graphs_use():
    samples = node_samples("m1", parse(FIXTURE.read_text()))
    names = {s.name for s in samples}
    assert "go_goroutines" not in names and "node_network_receive_bytes_total" not in names
    assert "node_load1" not in names and "node_memory_Shmem_bytes" not in names
    cpu = {s.labels["mode"]: s.value for s in samples if s.name == "node_cpu_seconds_total"}
    assert cpu["idle"] == 2000 and cpu["user"] == 200 and len(cpu) == 8
    assert all(s.labels["member"] == "m1" for s in samples)
    disks = {s.labels["device"] for s in samples if s.name.startswith("node_disk_")}
    assert disks == {"nvme0n1", "sda"}
    mounts = {s.labels["mountpoint"] for s in samples if s.name.startswith("node_filesystem_")}
    assert mounts == {"/", "/boot/efi"}
    kinds = {s.name: s.kind for s in samples}
    assert kinds["node_pressure_io_waiting_seconds_total"] == "counter"
    assert kinds["node_vmstat_oom_kill"] == "counter"
    assert kinds["node_memory_MemAvailable_bytes"] == "gauge"
    assert kinds["node_cpu_seconds_total"] == "counter"


def test_host_now_from_one_and_two_scrapes():
    cur = parse(FIXTURE.read_text())
    one = host_now(cur, None, 0)
    gib = 1024**3
    assert one["memory_total"] == 32 * gib and one["memory_used"] == 16 * gib
    assert one["memory_cache"] == 11 * gib and one["zfs_arc"] == 2 * gib
    assert one["swap_total"] == 4 * gib and one["swap_used"] == 1 * gib
    assert one["rootfs_size"] == 1e11 and one["rootfs_used"] == 6e10
    assert one["timex_sync"] == 1 and "cpu_percent" not in one
    two = host_now(parse(later(FIXTURE.read_text())), cur, 10)
    assert two["cpu_percent"] == pytest.approx(30)
    assert two["cpu_iowait_percent"] == pytest.approx(10)
    assert two["cpu_steal_percent"] == pytest.approx(10)
    assert two["disk_busiest"] == "sda" and two["disk_busy_percent"] == pytest.approx(50)
    assert two["disk_read_bps"] == pytest.approx(200)  # rbd0 isn't a host disk
    assert two["disk_write_bps"] == pytest.approx(500)
    assert two["pressure_cpu"] == pytest.approx(5)
    assert two["pressure_io"] == pytest.approx(10)
    assert two["pressure_memory"] == 0
    bare = host_now(parse('node_cpu_seconds_total{mode="idle"} 1\n'), parse(""), 10)
    assert bare["memory_used"] is None and bare["memory_cache"] is None
    assert bare["swap_used"] is None and "rootfs_used" not in bare and "cpu_percent" not in bare


@respx.mock
async def test_scrape_keeps_two_rounds_for_rates(scraper, clock):
    text = FIXTURE.read_text()
    respx.get(U1).mock(
        side_effect=[httpx.Response(200, text=text)] + [httpx.Response(200, text=later(text))] * 2
    )
    respx.get(U2).mock(side_effect=httpx.ConnectError("Connection refused"))
    assert scraper.now("m1") is None
    res = await scraper.scrape_all([M1, M2])
    assert [r.ok for r in res] == [True, False]
    assert res[1].absent and res[1].error == "not reachable (not installed?)"
    assert "cpu_percent" not in scraper.now("m1")
    assert scraper.now("m2") == {
        "url": "https://192.0.2.2:9100",
        "ok": False,
        "error": res[1].error,
        "absent": True,
    }
    clock[0] += 10
    await scraper.scrape_all([M1, M2])
    now = scraper.now("m1")
    assert now["ok"] and now["cpu_percent"] == pytest.approx(30)
    # m2 leaves the cluster: forgotten
    await scraper.scrape_all([M1])
    assert scraper.now("m2") is None and "m2" not in scraper.prev


@respx.mock
async def test_scrape_failures(scraper):
    respx.get(U1).mock(
        side_effect=[
            httpx.Response(401),
            httpx.Response(500),
            httpx.Response(200, text="# just comments\nup 1\n"),
            httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"),
            httpx.ReadTimeout("timed out"),
            httpx.RemoteProtocolError("bad"),
            httpx.Response(200, text=FIXTURE.read_text()),
            httpx.Response(500),
        ]
    )
    errors = []
    for _ in range(6):
        r = await scraper.scrape_member(M1)
        errors.append((r.ok, r.error, r.absent))
    assert errors == [
        (False, "HTTP 401: check username/password", False),
        (False, "HTTP 500", False),
        (False, "not a node_exporter", False),
        (False, "TLS: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed", False),
        (False, "ReadTimeout: timed out", True),
        (False, "RemoteProtocolError: bad", False),
    ]
    await scraper.scrape_all([M1])
    await scraper.scrape_all([M1])
    assert "m1" not in scraper.prev  # a failed round drops the rate baseline


@respx.mock
async def test_auth_and_sni(tmp_path, certs):
    pw = tmp_path / "pw"
    pw.write_text("s3cret\n")
    pw.chmod(0o600)
    s = NodeExporterSettings(
        username="mcui", password_file=pw, ca=certs.server_cert, server_name="node-exporter"
    )
    scraper = NodeScraper(s)
    route = respx.get(U1).mock(return_value=httpx.Response(200, text=FIXTURE.read_text()))
    assert (await scraper.scrape_member(M1)).ok
    req = route.calls.last.request
    assert req.headers["authorization"] == "Basic bWN1aTpzM2NyZXQ="
    assert req.extensions["sni_hostname"] == "node-exporter"
    await scraper.aclose()
    assert _ssl_context(NodeExporterSettings(scheme="http")) is False


def test_settings(tmp_path, certs):
    pw = tmp_path / "pw"
    pw.write_text("x")
    pw.chmod(0o644)
    with pytest.raises(ValueError, match="together"):
        NodeExporterSettings(username="mcui")
    with pytest.raises(ValueError, match="group/other"):
        NodeExporterSettings(username="mcui", password_file=pw)
    with pytest.raises(ValueError, match="file not found"):
        NodeExporterSettings(ca=tmp_path / "nope.crt")
    assert NodeExporterSettings().password() is None
    assert Settings().node_exporter.enabled


@pytest.fixture
async def store():
    s = MetricStore(SQLiteMetricStore(":memory:", clock=lambda: NOW))
    yield s
    await s.aclose()


@respx.mock
async def test_collector_writes_node_samples(lxd, store, settings, scraper):
    live = LiveMetrics(lxd, Scraper(lxd, settings.metrics, clock=lambda: NOW))
    c = Collector(lxd, live, store, settings, None, scraper, clock=lambda: NOW)
    live._update([M1, M2], [_ok("m1"), _ok("m2")])
    respx.get(U1).mock(return_value=httpx.Response(200, text=FIXTURE.read_text()))
    respx.get(U2).mock(side_effect=httpx.ConnectError("refused"))
    assert await c.collect_node() > 20  # m2 without node_exporter isn't an error
    r = store.backend.query("node_cpu_seconds_total", {"mode": "idle"}, NOW - 60, NOW + 60, res=RAW)
    assert r.series[0].labels == {"member": "m1", "mode": "idle"}
    respx.get(U2).mock(return_value=httpx.Response(401))
    with pytest.raises(RuntimeError, match="m2: HTTP 401"):
        await c.collect_node()
    await c._scrape_round()
    assert c.status["node"].last_error == "m2: HTTP 401: check username/password"
    assert await Collector(lxd, live, store, settings).collect_node() == 0


def _ok(member):
    return MemberScrape(member, f"https://{member}:8443", True, NOW)
