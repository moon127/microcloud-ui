import json

import httpx
import respx

from mcui.record import Scrubber, record
from tests.conftest import EP1, fixture_json, lxd_ok

# lists recorded besides members, instances, pools, networks and warnings
LISTS = ["profiles", "images", "projects", "network-acls", "network-allocations", "operations"]


def lxd_only(settings):
    """No Ceph mgr or node_exporter, so only LXD has to be mocked."""
    off = {"enabled": False}
    return settings.model_copy(
        update={
            "ceph": settings.ceph.model_copy(update=off),
            "node_exporter": settings.node_exporter.model_copy(update=off),
        }
    )


def mock_lists():
    for name in LISTS:
        respx.get(f"{EP1}/1.0/{name}").mock(return_value=lxd_ok([]))


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
    respx.get(f"{EP1}/1.0/resources").mock(
        return_value=lxd_ok({"system": {"serial": "ABC123", "uuid": "u-1", "vendor": "X"}})
    )
    respx.get("https://10.20.30.1:8443/1.0/metrics").mock(
        return_value=httpx.Response(200, text='lxd_uptime_seconds{addr="10.20.30.1"} 1\n')
    )
    respx.get("https://192.0.2.2:8443/1.0/metrics").mock(return_value=httpx.Response(403))
    mock_lists()

    out = tmp_path / "rec"
    log: list[str] = []
    failures = await record(lxd_only(settings), lxd, lxd, out, log.append)
    assert failures == 2  # networks + m2 metrics
    names = sorted(p.name for p in out.iterdir())
    assert names == [
        "1.0.json",
        "1.0_cluster_members_node-1_state.json",
        "1.0_cluster_members_node-2_state.json",
        "1.0_cluster_members_recursion_1.json",
        "1.0_images_recursion_1_all-projects_true.json",
        "1.0_instances_recursion_2_all-projects_true.json",
        "1.0_network-acls_recursion_1_all-projects_true.json",
        "1.0_network-allocations_all-projects_true.json",
        "1.0_operations_recursion_1_all-projects_true.json",
        "1.0_profiles_recursion_1_all-projects_true.json",
        "1.0_projects_recursion_1.json",
        "1.0_resources_target_node-1.json",
        "1.0_resources_target_node-2.json",
        "1.0_storage-pools_recursion_1.json",
        "1.0_warnings_recursion_1.json",
        "metrics_node-1.txt",
    ]
    everything = "".join(p.read_text() for p in out.iterdir())
    assert "10.20.30.1" not in everything
    assert (out / "1.0_storage-pools_recursion_1.json").read_text() == "not json"
    res = json.loads((out / "1.0_resources_target_node-1.json").read_text())["metadata"]["system"]
    assert res == {"serial": "SCRUBBED", "uuid": "SCRUBBED", "vendor": "X"}
    assert any("FAIL metrics m2" in line for line in log)
    assert log[-1].startswith("Scrubbed ")


@respx.mock
async def test_record_pools_standalone(settings, lxd, tmp_path):
    respx.get(f"{EP1}/1.0").mock(return_value=lxd_ok(fixture_json("server_standalone.json")))
    respx.get(f"{EP1}/1.0/cluster/members").mock(return_value=lxd_ok([]))
    respx.get(f"{EP1}/1.0/instances").mock(return_value=lxd_ok([]))
    respx.get(f"{EP1}/1.0/networks").mock(return_value=lxd_ok([]))
    respx.get(f"{EP1}/1.0/warnings").mock(return_value=lxd_ok([]))
    respx.get(f"{EP1}/1.0/storage-pools", params={"recursion": "1"}).mock(return_value=lxd_ok([]))
    respx.get(f"{EP1}/1.0/storage-pools").mock(return_value=lxd_ok(["/1.0/storage-pools/lz"]))
    respx.get(f"{EP1}/1.0/storage-pools/lz").mock(return_value=lxd_ok({"driver": "zfs"}))
    respx.get(f"{EP1}/1.0/storage-pools/lz/resources").mock(return_value=lxd_ok({"space": {}}))
    respx.get(f"{EP1}/1.0/storage-pools/lz/volumes").mock(return_value=lxd_ok([]))
    respx.get(f"{EP1}/1.0/metrics").mock(return_value=httpx.Response(200, text="x 1\n"))
    mock_lists()
    out = tmp_path / "rec"
    assert await record(lxd_only(settings), lxd, lxd, out, lambda _: None) == 0
    names = sorted(p.name for p in out.iterdir())
    assert "1.0_storage-pools_lz.json" in names
    assert "1.0_storage-pools_lz_resources.json" in names
    assert "1.0_storage-pools_lz_volumes_recursion_1_all-projects_true.json" in names


@respx.mock
async def test_record_networks_ceph_and_hosts(settings, lxd, tmp_path):
    """Each managed network's sub-resources, the Ceph mgr and node_exporter (Phase 5.0)."""
    md = fixture_json("server_standalone.json")
    # an LXD 6 server: its own objects are recorded too
    md["api_extensions"] += [
        "network_load_balancer_pool",
        "instance_placement_groups",
        "image_registries",
    ]
    respx.get(f"{EP1}/1.0").mock(return_value=lxd_ok(md))
    respx.get(f"{EP1}/1.0/cluster/members").mock(return_value=lxd_ok([]))
    respx.get(f"{EP1}/1.0/instances").mock(return_value=lxd_ok([]))
    respx.get(f"{EP1}/1.0/warnings").mock(return_value=lxd_ok([]))
    respx.get(f"{EP1}/1.0/storage-pools").mock(return_value=lxd_ok([]))
    for name in ("placement-groups", "image-registries", "networks/default/load-balancer-pools"):
        respx.get(f"{EP1}/1.0/{name}").mock(return_value=lxd_ok([]))
    mock_lists()
    nets = [
        {"name": "UPLINK", "type": "physical", "managed": True},
        {"name": "default", "type": "ovn", "managed": True},
        {"name": "lxdbr0", "type": "bridge", "managed": True},
        {"name": "eth0", "type": "physical", "managed": False},
    ]
    # only the detailed list (recursion=1) says which networks are managed and their type
    respx.get(f"{EP1}/1.0/networks", params={"recursion": "1"}).mock(return_value=lxd_ok(nets))
    respx.get(f"{EP1}/1.0/networks").mock(
        return_value=lxd_ok([f"/1.0/networks/{n['name']}" for n in nets])
    )
    for path in (
        "networks/UPLINK/leases",
        "networks/UPLINK/state",
        "networks/default/forwards",
        "networks/default/load-balancers",
        "networks/default/peers",
        "networks/default/state",
        "networks/lxdbr0/leases",
        "networks/lxdbr0/state",
    ):
        respx.get(f"{EP1}/1.0/{path}").mock(return_value=lxd_ok([]))
    respx.get(f"{EP1}/1.0/metrics").mock(return_value=httpx.Response(200, text="x 1\n"))
    # one mgr answers with data, node_exporter isn't installed
    respx.get("http://192.0.2.1:9283/metrics").mock(
        return_value=httpx.Response(200, text='ceph_health_status{addr="10.20.30.9"} 0\n')
    )
    respx.get("https://192.0.2.1:9100/metrics").mock(side_effect=httpx.ConnectError("refused"))
    out = tmp_path / "rec"
    log: list[str] = []
    assert await record(settings, lxd, lxd, out, log.append) == 0  # optional sources
    names = {p.name for p in out.iterdir()}
    assert {
        "1.0_networks_default_forwards_recursion_1.json",
        "1.0_networks_default_load-balancers_recursion_1.json",
        "1.0_networks_default_peers_recursion_1.json",
        "1.0_networks_default_load-balancer-pools_recursion_1.json",
        "1.0_placement-groups_recursion_1_all-projects_true.json",
        "1.0_image-registries_recursion_1.json",
        "1.0_networks_default_state.json",
        "1.0_networks_lxdbr0_leases.json",
        "1.0_networks_lxdbr0_state.json",
        "ceph_mgr.txt",
    } <= names
    assert not any(n.startswith("1.0_networks_eth0") for n in names)  # unmanaged
    assert "10.20.30.9" not in (out / "ceph_mgr.txt").read_text()  # scrubbed
    assert any("no node_exporter on" in line for line in log)


def test_member_names_and_secret_keys_are_scrubbed():
    """Real hostnames and secret-carrying config values never reach a fixture (D89)."""
    from mcui.record import Scrubber, redact

    s = Scrubber()
    s.names(["gw-a-2", "gw-a-1", "lab.example.com"])
    assert (
        s.scrub('{"location": "gw-a-1", "x": "gw-a-10"}')
        == '{"location": "node-1", "x": "gw-a-10"}'
    )
    assert s.scrub("1.0_resources_target_gw-a-2.json") == "1.0_resources_target_node-2.json"
    assert s.scrub("LAB.example.com:8443") == "node-3:8443"
    doc = {
        "config": {
            "cloud-init.user-data": "#cloud-config\nchpasswd: ...",
            "user.user-data": "x",
            "environment.API_TOKEN": "t",
            "bgp.peers.r1.password": "p",
            "loki.auth.password": "",
            "limits.cpu": "2",
        },
        "metadata": {"fds": {"0": "s"}},
    }
    out = redact(doc)
    assert out["config"] == {
        "cloud-init.user-data": "SCRUBBED",
        "user.user-data": "SCRUBBED",
        "environment.API_TOKEN": "SCRUBBED",
        "bgp.peers.r1.password": "SCRUBBED",
        "loki.auth.password": "",  # empty stays empty
        "limits.cpu": "2",
    }
    assert out["metadata"]["fds"] == "SCRUBBED"


def test_disk_ids_and_extra_words():
    """By-id names keep only their kind; --scrub-word replaces anything else (D90)."""
    from mcui.record import Scrubber, redact

    assert redact({"device_id": "wwn-0x5001b444a553db8f"}) == {"device_id": "wwn-SCRUBBED"}
    assert redact({"device_id": "ata-CT120_1234ABCD"}) == {"device_id": "ata-SCRUBBED"}
    assert redact({"device_id": ""}) == {"device_id": ""}
    s = Scrubber()
    s.words(["example.net", ""])
    assert s.scrub("a.host.EXAMPLE.net DNS") == "a.host.scrubbed-1.example DNS"
