import respx

from mcui.lxd.server import ADAPTS, FEATURES, ServerInfo, features, members, server_info
from tests.conftest import EP1, fixture_json, lxd_ok


def test_features_flags():
    f = features(["metrics", "network_peer"])
    assert set(f) == set(FEATURES) | set(ADAPTS)
    assert f["metrics"] and f["network_peers"]
    assert not f["clustering"] and not f["storage_buckets"]


def test_server_info_from_metadata():
    info = ServerInfo.from_metadata(fixture_json("server_clustered.json"))
    assert info.version == "5.21.8"
    assert info.clustered and info.trusted
    assert info.auth_method == "tls"
    d = info.as_dict()
    assert d["server_name"] == "m1"
    # not in this 5.21 fixture
    lxd6 = {
        "placement_groups",
        "snapshot_volumes",
        "load_balancer_pools",
        "load_balancer_health_checks",
    }
    assert all(d["features"][k] for k in FEATURES if k not in lxd6)
    assert not any(d["features"][k] for k in lxd6)
    assert d["features"]["cephfs_path"] is False  # LXD 5.21 takes source


def test_adapts_on_lxd_6_10():
    md = fixture_json("server_clustered.json")
    md["api_extensions"] = [*md["api_extensions"], "storage_remote_drop_source"]
    assert ServerInfo.from_metadata(md).features()["cephfs_path"] is True


def test_server_info_untrusted_minimal():
    info = ServerInfo.from_metadata({"auth": "untrusted"})
    assert not info.trusted and not info.clustered and info.api_extensions == []


@respx.mock
async def test_members_clustered(lxd):
    respx.get(f"{EP1}/1.0").mock(return_value=lxd_ok(fixture_json("server_clustered.json")))
    respx.get(f"{EP1}/1.0/cluster/members", params={"recursion": "1"}).mock(
        return_value=lxd_ok(fixture_json("cluster_members.json"))
    )
    ms = await members(lxd)
    assert [m.name for m in ms] == ["m1", "m2"]
    assert ms[0].roles == ["database-leader", "database"]
    assert ms[1].as_dict()["url"] == "https://192.0.2.2:8443"


@respx.mock
async def test_members_standalone(lxd):
    respx.get(f"{EP1}/1.0").mock(return_value=lxd_ok(fixture_json("server_standalone.json")))
    info = await server_info(lxd)
    ms = await members(lxd, info)
    assert [(m.name, m.url, m.status) for m in ms] == [("solo", EP1, "Online")]
