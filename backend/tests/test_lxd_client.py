import ssl

import httpx
import pytest
import respx

from mcui.lxd.client import LXDClient, LXDError, LXDUnavailable
from tests.conftest import EP1, EP2, SERVER_NAME, lxd_err, lxd_ok


@respx.mock
async def test_query_returns_metadata_and_sends_sni(lxd):
    route = respx.get(f"{EP1}/1.0/instances", params={"recursion": "1"}).mock(
        return_value=lxd_ok([{"name": "c1"}])
    )
    assert await lxd.get("/1.0/instances", recursion=1) == [{"name": "c1"}]
    assert route.calls.last.request.extensions["sni_hostname"] == SERVER_NAME


@respx.mock
async def test_no_sni_extension_without_server_name():
    c = LXDClient([EP1], verify=ssl.create_default_context())
    route = respx.get(f"{EP1}/1.0").mock(return_value=lxd_ok({}))
    await c.get("/1.0")
    assert "sni_hostname" not in route.calls.last.request.extensions
    await c.aclose()


@respx.mock
async def test_lxd_error_body(lxd):
    respx.get(f"{EP1}/1.0/instances/x").mock(return_value=lxd_err("Instance not found", 404))
    with pytest.raises(LXDError, match="Instance not found") as e:
        await lxd.get("/1.0/instances/x")
    assert e.value.status == 404


@respx.mock
async def test_http_error_without_error_field(lxd):
    respx.get(f"{EP1}/1.0").mock(return_value=httpx.Response(500, json={"type": "sync"}))
    with pytest.raises(LXDError, match="HTTP 500"):
        await lxd.get("/1.0")


@respx.mock
async def test_non_json(lxd):
    respx.get(f"{EP1}/1.0").mock(return_value=httpx.Response(502, text="<html>"))
    with pytest.raises(LXDError, match="non-JSON"):
        await lxd.get("/1.0")


@respx.mock
async def test_non_object_json(lxd):
    respx.get(f"{EP1}/1.0").mock(return_value=httpx.Response(200, json=[1]))
    with pytest.raises(LXDError, match="unexpected response"):
        await lxd.get("/1.0")


@respx.mock
async def test_failover_and_sticky(lxd):
    r1 = respx.get(f"{EP1}/1.0").mock(side_effect=httpx.ConnectError("refused"))
    r2 = respx.get(f"{EP2}/1.0").mock(return_value=lxd_ok({"ok": 1}))
    assert await lxd.get("/1.0") == {"ok": 1}
    assert lxd.endpoint == EP2
    await lxd.get("/1.0")
    assert r1.call_count == 1  # second call went straight to EP2
    assert r2.call_count == 2


@respx.mock
async def test_all_endpoints_down(lxd):
    respx.get(f"{EP1}/1.0").mock(side_effect=httpx.ConnectError("refused"))
    respx.get(f"{EP2}/1.0").mock(side_effect=httpx.ConnectTimeout(""))
    with pytest.raises(LXDUnavailable) as e:
        await lxd.get("/1.0")
    assert "ConnectError: refused" in str(e.value)
    assert f"{EP2}: ConnectTimeout" in str(e.value)


@respx.mock
async def test_base_targets_one_server_without_failover(lxd):
    respx.get("https://192.0.2.9:8444/1.0/metrics").mock(side_effect=httpx.ConnectError("no"))
    with pytest.raises(LXDUnavailable, match="^ConnectError: no$"):
        await lxd.raw("GET", "/1.0/metrics", base="https://192.0.2.9:8444/")
    respx.get("https://192.0.2.9:8443/1.0").mock(return_value=lxd_ok({"x": 1}))
    assert await lxd.query("GET", "/1.0", base="https://192.0.2.9:8443") == {"x": 1}
    assert lxd.endpoint == EP1


def test_requires_endpoint():
    with pytest.raises(ValueError):
        LXDClient([], verify=ssl.create_default_context())


async def test_from_files(certs):
    c = LXDClient.from_files(
        [EP1],
        server_cert=certs.server_cert,
        client_cert=certs.client_cert,
        client_key=certs.client_key,
        server_name=SERVER_NAME,
        timeout=3,
    )
    assert c.server_name == SERVER_NAME
    await c.aclose()
