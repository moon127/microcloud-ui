import json

import httpx
import respx

from mcui.record import Scrubber, record
from tests.conftest import EP1, fixture_json, lxd_ok


def test_scrubber_consistent_and_safe():
    s = Scrubber()
    text = (
        "a 10.1.2.3 b 10.1.2.3 c 10.9.9.9 lo 127.0.0.1 any 0.0.0.0 bad 999.1.1.1\n"
        "v6 fd42:1:2::5 fe80::216:3eff:fe12:3456 keep ::1 time 19:44:57 ver 1.2.3\n"
        "mac 00:16:3E:12:34:56 again 00:16:3e:12:34:56\n"
        f"fp {'ab' * 32} long {'c' * 70}\n"
        "-----BEGIN CERTIFICATE-----\nMIIB\nxyz\n-----END CERTIFICATE-----\n"
    )
    out = s.scrub(text)
    assert "a 192.0.2.2 b 192.0.2.2 c 192.0.2.3" in out
    assert "lo 127.0.0.1 any 0.0.0.0 bad 999.1.1.1" in out
    assert "v6 2001:db8::1 2001:db8::2 keep ::1 time 19:44:57 ver 1.2.3" in out
    assert "mac 00:16:3e:00:00:01 again 00:16:3e:00:00:01" in out
    assert f"fp {1:064x}" in out and "c" * 70 in out
    assert "MIIB" not in out and "SCRUBBED" in out


def test_scrubber_pem_inside_json():
    s = Scrubber()
    doc = json.dumps(
        {"certificate": "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"}
    )
    out = json.loads(s.scrub(doc))
    assert (
        out["certificate"] == "-----BEGIN CERTIFICATE-----\nSCRUBBED\n-----END CERTIFICATE-----\n"
    )


@respx.mock
async def test_record_writes_scrubbed_files(settings, lxd, tmp_path):
    md = fixture_json("server_clustered.json")
    md["environment"]["addresses"] = ["10.20.30.1:8443"]
    respx.get(f"{EP1}/1.0").mock(return_value=lxd_ok(md))
    members = fixture_json("cluster_members.json")
    members[0]["url"] = "https://10.20.30.1:8443"
    respx.get(f"{EP1}/1.0/cluster/members").mock(return_value=lxd_ok(members))
    respx.get(f"{EP1}/1.0/instances").mock(return_value=lxd_ok([]))
    respx.get(f"{EP1}/1.0/storage-pools").mock(return_value=httpx.Response(200, text="not json"))
    respx.get(f"{EP1}/1.0/networks").mock(side_effect=httpx.ConnectError("x"))
    respx.get("https://192.0.2.2:8443/1.0/networks").mock(side_effect=httpx.ConnectError("x"))
    respx.get(f"{EP1}/1.0/warnings").mock(return_value=lxd_ok([]))
    respx.get(f"{EP1}/1.0/cluster/members/m1/state").mock(return_value=lxd_ok({}))
    respx.get(f"{EP1}/1.0/cluster/members/m2/state").mock(return_value=lxd_ok({}))
    respx.get("https://10.20.30.1:8443/1.0/metrics").mock(
        return_value=httpx.Response(200, text='lxd_uptime_seconds{addr="10.20.30.1"} 1\n')
    )
    respx.get("https://192.0.2.2:8443/1.0/metrics").mock(return_value=httpx.Response(403))

    out = tmp_path / "rec"
    log: list[str] = []
    failures = await record(settings, lxd, lxd, out, log.append)
    assert failures == 2  # networks + m2 metrics
    names = sorted(p.name for p in out.iterdir())
    assert names == [
        "1.0.json",
        "1.0_cluster_members_m1_state.json",
        "1.0_cluster_members_m2_state.json",
        "1.0_cluster_members_recursion_1.json",
        "1.0_instances_recursion_2_all-projects_true.json",
        "1.0_storage-pools_recursion_1.json",
        "1.0_warnings_recursion_1.json",
        "metrics_m1.txt",
    ]
    everything = "".join(p.read_text() for p in out.iterdir())
    assert "10.20.30.1" not in everything
    assert (out / "1.0_storage-pools_recursion_1.json").read_text() == "not json"
    assert any("FAIL metrics m2" in line for line in log)
    assert log[-1].startswith("Scrubbed ")
