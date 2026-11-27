"""Exporter pages are read with a size cap (D89)."""

import httpx
import pytest
import respx

from mcui.metrics.http import BodyTooLarge, get_text


@respx.mock
async def test_get_text():
    respx.get("http://e/ok").mock(return_value=httpx.Response(200, text="node_x 1\n"))
    respx.get("http://e/big").mock(return_value=httpx.Response(200, text="x" * 100))
    respx.get("http://e/sneaky").mock(
        return_value=httpx.Response(200, stream=httpx.ByteStream(b"y" * 100))
    )
    async with httpx.AsyncClient() as c:
        assert await get_text(c, "http://e/ok") == (200, "node_x 1\n")
        with pytest.raises(BodyTooLarge, match="over the 10-byte limit"):
            await get_text(c, "http://e/big", limit=10)
        with pytest.raises(BodyTooLarge):
            await get_text(c, "http://e/sneaky", limit=10)


@respx.mock
async def test_scrapers_report_an_oversized_page(monkeypatch):
    """The scrapers treat it like any other HTTP error: not ok, with the reason."""
    from mcui.config import CephSettings
    from mcui.metrics.ceph import CephScraper

    monkeypatch.setattr("mcui.metrics.http.MAX_BODY", 10)
    monkeypatch.setattr("mcui.metrics.http.get_text.__defaults__", (10,))
    respx.get("http://192.0.2.1:9283/metrics").mock(return_value=httpx.Response(200, text="z" * 50))
    s = CephScraper(CephSettings(mgr_endpoints=["http://192.0.2.1:9283"]))
    res = await s._one("http://192.0.2.1:9283")
    assert not res.ok and "BodyTooLarge" in (res.error or "")
    await s._http.aclose()
