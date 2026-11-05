import httpx
import respx

from mcui.check import run_check
from tests.conftest import EP1, EP2, fixture_json, fixture_text, lxd_ok, make_settings


def mock_good(clustered=True):
    md = fixture_json("server_clustered.json" if clustered else "server_standalone.json")
    respx.get(f"{EP1}/1.0").mock(return_value=lxd_ok(md))
    respx.get(f"{EP2}/1.0").mock(return_value=lxd_ok(md))
    respx.get(f"{EP1}/1.0/cluster/members", params={"recursion": "1"}).mock(
        return_value=lxd_ok(fixture_json("cluster_members.json"))
    )
    for ip in ("192.0.2.1", "192.0.2.2"):
        respx.get(f"https://{ip}:8443/1.0/metrics").mock(
            return_value=httpx.Response(200, text=fixture_text("metrics_m2_t0.txt"))
        )


async def run(settings, lxd):
    lines: list[str] = []
    ok = await run_check(settings, lxd, lxd, lines.append)
    return ok, "\n".join(lines)


@respx.mock
async def test_all_good(settings, lxd):
    mock_good()
    ok, out = await run(settings, lxd)
    assert ok, out
    assert f"ok   {EP1}: LXD 5.21.8, clustered, server m1, auth tls" in out
    assert "Cluster members (2):" in out and "ok   m2: Online" in out
    assert "Metrics (port 8443, management cert):" in out
    assert "ok   m1: 3 samples (8443)" in out
    assert "Unavailable" not in out and out.endswith("Result: OK")


@respx.mock
async def test_standalone_missing_features_and_metrics_cert(certs, tmp_path, lxd):
    s = make_settings(
        certs, tmp_path, metrics={"cert": str(certs.other_cert), "key": str(certs.other_key)}
    )
    mock_good(clustered=False)
    ok, out = await run(s, lxd)
    assert ok, out
    assert "standalone" in out and "Cluster members (1):" in out
    assert "metrics cert" in out
    assert "Unavailable (hidden in the UI): " in out and "clustering" in out


@respx.mock
async def test_endpoint_failures(settings, lxd):
    mock_good()
    respx.get(f"{EP1}/1.0").mock(side_effect=httpx.ConnectError("refused"))
    untrusted = {**fixture_json("server_clustered.json"), "auth": "untrusted"}
    respx.get(f"{EP2}/1.0").mock(return_value=lxd_ok(untrusted))
    ok, out = await run(settings, lxd)
    assert not ok
    assert f"FAIL {EP1}: ConnectError: refused" in out
    assert "not trusted (auth='untrusted')" in out
    assert out.endswith("No trusted LXD endpoint; cannot continue.")


@respx.mock
async def test_members_fail(settings, lxd):
    mock_good()
    respx.get(f"{EP1}/1.0/cluster/members", params={"recursion": "1"}).mock(
        return_value=httpx.Response(500, json={})
    )
    ok, out = await run(settings, lxd)
    assert not ok and "Cluster members: FAIL HTTP 500" in out


@respx.mock
async def test_metrics_and_member_problems(settings, lxd):
    mock_good()
    members = fixture_json("cluster_members.json")
    members[1]["status"] = "Offline"
    respx.get(f"{EP1}/1.0/cluster/members", params={"recursion": "1"}).mock(
        return_value=lxd_ok(members)
    )
    respx.get("https://192.0.2.2:8443/1.0/metrics").mock(return_value=httpx.Response(403))
    ok, out = await run(settings, lxd)
    assert not ok
    assert "WARN m2: Offline" in out
    assert "FAIL m2: https://192.0.2.2:8443: metrics certificate not trusted" in out
    assert out.endswith("Result: FAILED")


@respx.mock
async def test_fallback_reported_and_no_metrics_extension(certs, tmp_path, lxd):
    s = make_settings(certs, tmp_path, metrics={"port": 8444, "fallback_to_api_port": True})
    mock_good()
    md = {**fixture_json("server_clustered.json"), "api_extensions": ["clustering"]}
    respx.get(f"{EP1}/1.0").mock(return_value=lxd_ok(md))
    respx.get(f"{EP2}/1.0").mock(return_value=lxd_ok(md))
    respx.get("https://192.0.2.1:8444/1.0/metrics").mock(side_effect=httpx.ConnectError("x"))
    respx.get("https://192.0.2.2:8444/1.0/metrics").mock(side_effect=httpx.ConnectError("x"))
    ok, out = await run(s, lxd)
    assert not ok
    assert "(8443) via fallback to API port" in out
    assert "FAIL: this LXD has no metrics API extension" in out
