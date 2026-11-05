import httpx
import pytest
import respx

from mcui.config import MetricsSettings
from mcui.lxd.server import Member
from mcui.metrics.scrape import Scraper, metrics_url, with_port
from tests.conftest import fixture_text

M1 = Member("m1", "https://192.0.2.1:8443", "Online")
M2 = Member("m2", "https://192.0.2.2:8443", "Online")


def scraper(lxd, **kw) -> Scraper:
    return Scraper(lxd, MetricsSettings(**kw), clock=lambda: 100.0)


@pytest.mark.parametrize(
    "url,port,want",
    [
        ("https://192.0.2.1:8443", 8444, "https://192.0.2.1:8444"),
        ("https://[2001:db8::1]:8443", 8444, "https://[2001:db8::1]:8444"),
        ("https://host", 9000, "https://host:9000"),
    ],
)
def test_with_port(url, port, want):
    assert with_port(url, port) == want


def test_metrics_url_override():
    s = MetricsSettings(port=8444, member_overrides={"m2": "https://192.0.2.22:9444/"})
    assert metrics_url(M1, s) == "https://192.0.2.1:8444"
    assert metrics_url(M2, s) == "https://192.0.2.22:9444"


@respx.mock
async def test_scrape_ok(lxd):
    respx.get("https://192.0.2.1:8443/1.0/metrics").mock(
        return_value=httpx.Response(200, text=fixture_text("metrics_m1_t0.txt"))
    )
    s = await scraper(lxd).scrape_member(M1)
    assert s.ok and s.at == 100.0 and s.port == 8443 and not s.fell_back
    assert s.raw.startswith("# HELP")
    assert s.status()["samples"] == len(s.exposition.samples) > 10


@respx.mock
async def test_scrape_403_and_500(lxd):
    respx.get("https://192.0.2.1:8443/1.0/metrics").mock(return_value=httpx.Response(403))
    respx.get("https://192.0.2.2:8443/1.0/metrics").mock(return_value=httpx.Response(500))
    s1, s2 = await scraper(lxd).scrape_all([M1, M2])
    assert not s1.ok and "not trusted" in s1.error
    assert not s2.ok and s2.error == "HTTP 500"


@respx.mock
async def test_unreachable_without_fallback(lxd):
    respx.get("https://192.0.2.1:8444/1.0/metrics").mock(side_effect=httpx.ConnectError("x"))
    s = await scraper(lxd, port=8444).scrape_member(M1)
    assert not s.ok and s.url.endswith(":8444") and "ConnectError" in s.error


@respx.mock
async def test_fallback_to_api_port(lxd):
    respx.get("https://192.0.2.1:8444/1.0/metrics").mock(side_effect=httpx.ConnectError("x"))
    respx.get("https://192.0.2.1:8443/1.0/metrics").mock(
        return_value=httpx.Response(200, text="a 1")
    )
    s = await scraper(lxd, port=8444, fallback_to_api_port=True).scrape_member(M1)
    assert s.ok and s.fell_back and s.port == 8443


@respx.mock
async def test_fallback_also_fails(lxd):
    respx.get("https://192.0.2.1:8444/1.0/metrics").mock(side_effect=httpx.ConnectError("x"))
    respx.get("https://192.0.2.1:8443/1.0/metrics").mock(side_effect=httpx.ConnectError("y"))
    s = await scraper(lxd, port=8444, fallback_to_api_port=True).scrape_member(M1)
    assert not s.ok and s.port == 8443 and "y" in s.error


@respx.mock
async def test_no_fallback_when_already_on_api_port(lxd):
    route = respx.get("https://192.0.2.1:8443/1.0/metrics").mock(
        side_effect=httpx.ConnectError("x")
    )
    s = await scraper(lxd, fallback_to_api_port=True).scrape_member(M1)
    assert not s.ok and route.call_count == 1
