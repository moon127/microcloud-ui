import httpx
import respx

from mcui.config import CephSettings
from mcui.lxd.server import Member
from mcui.metrics.ceph import CephScraper, mgr_urls

MEMBERS = [
    Member("m1", "https://192.0.2.1:8443", "Online"),
    Member("m2", "https://[2001:db8::2]:8443", "Online"),
]
DATA = "# TYPE ceph_health_status gauge\nceph_health_status 0\n"


def test_mgr_urls():
    assert mgr_urls(CephSettings(), MEMBERS) == [
        "http://192.0.2.1:9283",
        "http://[2001:db8::2]:9283",
    ]
    s = CephSettings(mgr_endpoints=["http://10.0.0.1:9283/"], mgr_port=1)
    assert mgr_urls(s, MEMBERS) == ["http://10.0.0.1:9283"]


@respx.mock
async def test_finds_active_and_sticks_to_it():
    a = respx.get("http://192.0.2.1:9283/metrics").mock(return_value=httpx.Response(200, text=""))
    b = respx.get("http://[2001:db8::2]:9283/metrics").mock(
        return_value=httpx.Response(200, text=DATA)
    )
    c = CephScraper(CephSettings())
    res = await c.scrape(MEMBERS)
    assert res.ok and res.url == "http://[2001:db8::2]:9283" and res.exposition.samples
    assert c.active == res.url
    await c.scrape(MEMBERS)
    assert b.call_count == 2 and a.call_count == 1  # second round only asked the active mgr
    # failover: the active mgr goes standby, the other one takes over
    b.mock(return_value=httpx.Response(200, text=""))
    a.mock(return_value=httpx.Response(200, text=DATA))
    res = await c.scrape(MEMBERS)
    assert res.ok and c.active == "http://192.0.2.1:9283"
    await c.aclose()


@respx.mock
async def test_no_active_mgr():
    respx.get("http://192.0.2.1:9283/metrics").mock(side_effect=httpx.ConnectError("refused"))
    respx.get("http://[2001:db8::2]:9283/metrics").mock(return_value=httpx.Response(503))
    c = CephScraper(CephSettings())
    c.active = "http://192.0.2.1:9283"
    res = await c.scrape(MEMBERS)
    assert not res.ok and c.active is None
    assert "ConnectError: refused" in res.error and "HTTP 503" in res.error
    res = await c.scrape([])
    assert "no mgr endpoints" in res.error
    await c.aclose()
