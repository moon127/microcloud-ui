import httpx
import pytest
import respx

from mcui.config import MetricsSettings
from mcui.metrics.live import LiveMetrics, counter_rate, daemon_values, instance_totals
from mcui.metrics.prom import parse
from mcui.metrics.scrape import Scraper
from tests.conftest import EP1, fixture_json, fixture_text, lxd_ok

M1_METRICS = "https://192.0.2.1:8443/1.0/metrics"
M2_METRICS = "https://192.0.2.2:8443/1.0/metrics"


def test_counter_rate():
    assert counter_rate(10, 40, 15) == 2
    assert counter_rate(100, 30, 15) == 2  # reset: the new value is the increase
    assert counter_rate(None, 1, 15) is None
    assert counter_rate(1, None, 15) is None
    assert counter_rate(1, 2, 0) is None


def test_instance_totals():
    t = instance_totals(parse(fixture_text("metrics_m1_t0.txt")))
    c1 = t[("default", "c1")]
    assert c1.type == "container"
    assert c1.values["cpu_busy"] == 30
    assert c1.values["disk_read"] == 11000  # summed over devices

    exp = parse(
        'lxd_disk_read_bytes_total{name="v",project="p",device="sda"} 100\n'
        'lxd_disk_read_bytes_total{name="v",project="p",device="sda1"} 90\n'
        'lxd_disk_read_bytes_total{name="v",project="p",device="loop3"} 5\n'
        'lxd_disk_read_bytes_total{name="v",project="p",device="dm-0"} 7\n'
        'lxd_disk_read_bytes_total{name="v",project="p",device="dm-1"} 7\n'
    )
    assert instance_totals(exp)[("p", "v")].values["disk_read"] == 100  # no double count
    assert c1.values["net_rx"] == 3000  # lo excluded
    assert c1.values["rootfs_size"] == 1e10 and c1.values["rootfs_avail"] == 3e9
    v1 = t[("web", "v1")]
    assert v1.values["cpu_busy"] == 50  # idle and iowait excluded
    assert v1.values["rootfs_avail"] == 6e9  # "/" only, not /data
    assert v1.rootfs_mountpoint == "/"


def test_rootfs_without_slash():
    """A VM running Podman: the agent reports /dev/sda1 at Podman's bind mount, not "/"."""
    fs = (
        'lxd_filesystem_%s_bytes{name="%s",project="p",'
        'device="%s",fstype="%s",mountpoint="%s"} %s\n'
    )
    rows = [
        ("pod", "/dev/sda13", "ext4", "/boot", 1e9, 5e8),
        ("pod", "/dev/sda15", "vfat", "/boot/efi", 1e8, 9e7),
        ("pod", "tmpfs", "tmpfs", "/run/lxd_agent", 5e7, 5e7),
        ("pod", "/dev/sda1", "ext4", "/var/lib/containers/storage/overlay", 8e10, 6e10),
        ("pod", "/dev/sdb", "ext4", "/data", 1e10, 1e10),
        ("odd", "/dev/sda1", "ext4", "/srv", 8e10, 6e10),  # size only: not usable
        ("none", "tmpfs", "tmpfs", "/run/x", 1e9, 1e9),
    ]
    text = ""
    for name, dev, fstype, mount, size, avail in rows:
        text += fs % ("size", name, dev, fstype, mount, size)
        if name != "odd":
            text += fs % ("avail", name, dev, fstype, mount, avail)
    t = instance_totals(parse(text))
    pod = t[("p", "pod")]
    assert pod.rootfs_mountpoint == "/var/lib/containers/storage/overlay"  # the largest
    assert pod.values["rootfs_size"] == 8e10 and pod.values["rootfs_avail"] == 6e10
    for name in ("odd", "none"):
        assert t[("p", name)].rootfs_mountpoint is None
        assert "rootfs_size" not in t[("p", name)].values


def test_daemon_values():
    d = daemon_values(parse(fixture_text("metrics_m1_t0.txt")))
    assert d == {
        "api_requests_ongoing": 3,
        "goroutines": 321,
        "uptime_seconds": 1000,
        "warnings": 3,
    }


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def mock_cluster():
    respx.get(f"{EP1}/1.0").mock(return_value=lxd_ok(fixture_json("server_clustered.json")))
    respx.get(f"{EP1}/1.0/cluster/members", params={"recursion": "1"}).mock(
        return_value=lxd_ok(fixture_json("cluster_members.json"))
    )


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def live(lxd, clock):
    return LiveMetrics(lxd, Scraper(lxd, MetricsSettings(), clock=clock))


def by_name(snap):
    return {i["name"]: i for i in snap["instances"]}


@respx.mock
async def test_two_scrapes_give_rates(live, clock):
    mock_cluster()
    m1 = respx.get(M1_METRICS)
    m1.side_effect = [
        httpx.Response(200, text=fixture_text("metrics_m1_t0.txt")),
        httpx.Response(200, text=fixture_text("metrics_m1_t15.txt")),
    ]
    respx.get(M2_METRICS).mock(
        return_value=httpx.Response(200, text=fixture_text("metrics_m2_t0.txt"))
    )

    first = await live.snapshot()
    assert first["scraped_at"] == 1000.0 and first["interval_seconds"] == 15
    inst = by_name(first)
    assert set(inst) == {"c1", "v1", "c2"}
    assert inst["c1"]["cpu_percent"] is None  # no previous sample yet
    assert inst["c1"]["memory_used"] == 5e8
    assert inst["c1"]["rootfs_used"] == 7e9
    assert inst["c2"]["member"] == "m2" and inst["c2"]["memory_used"] is None
    m = {x["name"]: x for x in first["members"]}
    assert m["m1"]["instances"] == 2 and m["m1"]["scrape"]["ok"]
    assert m["m1"]["daemon"]["goroutines"] == 321

    clock.t += 5  # under the 10 s minimum: served from cache
    assert (await live.snapshot())["scraped_at"] == 1000.0
    assert m1.call_count == 1

    clock.t += 10
    second = by_name(await live.snapshot())
    c1, v1 = second["c1"], second["v1"]
    assert c1["cpu_percent"] == pytest.approx(7 / 15 / 2 * 100)
    assert v1["cpus"] == 2  # VM: effective_total is 0, vCPUs counted
    assert v1["cpu_percent"] == pytest.approx(3 / 15 / 2 * 100)
    assert c1["disk_read_bps"] == pytest.approx(1000)
    assert c1["disk_write_bps"] == 0
    assert c1["net_rx_bps"] == pytest.approx(100)  # counter reset handled
    assert c1["procs"] == 42 and c1["oom_kills"] == 1
    assert second["c2"]["cpu_percent"] == 0


@respx.mock
async def test_failed_member_drops_history(live, clock):
    mock_cluster()
    respx.get(M1_METRICS).mock(
        return_value=httpx.Response(200, text=fixture_text("metrics_m1_t0.txt"))
    )
    m2 = respx.get(M2_METRICS)
    m2.side_effect = [
        httpx.Response(200, text=fixture_text("metrics_m2_t0.txt")),
        httpx.Response(403),
        httpx.Response(200, text=fixture_text("metrics_m2_t0.txt")),
    ]
    await live.snapshot()
    clock.t += 15
    snap = await live.snapshot()
    m = {x["name"]: x for x in snap["members"]}
    assert not m["m2"]["scrape"]["ok"] and m["m2"]["instances"] == 0 and m["m2"]["daemon"] == {}
    assert "c2" not in by_name(snap)
    clock.t += 15
    assert by_name(await live.snapshot())["c2"]["cpu_percent"] is None  # history restarted
