import json
from pathlib import Path

import respx

from mcui.lxd.instances import InstanceIndex, guest_nics, summarize
from tests.conftest import EP1, lxd_ok

RECORDED = Path(__file__).parent / "fixtures" / "recorded"


def recorded_instances():
    return json.loads((RECORDED / "1.0_instances_recursion_2_all-projects_true.json").read_text())[
        "metadata"
    ]


def test_guest_nics_from_real_cluster():
    by_name = {i["name"]: guest_nics(i) for i in recorded_instances()}
    assert by_name["podman-1"] == {"enp5s0": "legacy-infra"}  # VM: matched by MAC
    assert by_name["dns-legacy"] == {"eth0": "legacy-infra"}  # container
    assert by_name["runtipi"] == {"enp5s0": "legacy-infra"}  # docker/veth ignored
    assert by_name["UmbrelOS"] == {}  # stopped: no guest state


def test_guest_nics_edge_cases():
    inst = {
        "expanded_config": {"volatile.eth1.hwaddr": "AA:BB:CC:00:00:01"},
        "expanded_devices": {
            "eth0": {
                "type": "nic",
                "nictype": "bridged",
                "parent": "br0",
                "hwaddr": "aa:bb:cc:00:00:02",
            },
            "eth1": {"type": "nic", "network": "ovn1"},
            "eth2": {"type": "nic", "network": "nomac"},
            "root": {"type": "disk", "path": "/"},
        },
        "state": {
            "network": {
                "eth0": {"hwaddr": "AA:BB:CC:00:00:02"},
                "eth1": {"hwaddr": "aa:bb:cc:00:00:01"},
                "lo": {"hwaddr": ""},
            }
        },
    }
    assert guest_nics(inst) == {"eth0": "br0", "eth1": "ovn1"}
    assert guest_nics({}) == {}


def test_summarize():
    i = summarize({"name": "x", "type": "container", "status": "Running", "location": "m1"})
    assert i.as_dict() == {
        "project": "default",
        "name": "x",
        "type": "container",
        "status": "Running",
        "location": "m1",
        "description": "",
        "profiles": [],
        "nics": {},
        "stateful": False,
        "evacuate": "",
        "evacuated_from": "",
    }


@respx.mock
async def test_index_caches(lxd):
    route = respx.get(f"{EP1}/1.0/instances").mock(return_value=lxd_ok(recorded_instances()))
    now = [0.0]
    idx = InstanceIndex(lxd, max_age=60, clock=lambda: now[0])
    items = await idx.get()
    assert [i.name for i in items][:2] == ["CosmosCloud", "UmbrelOS"]
    assert route.calls.last.request.url.params["all-projects"] == "true"
    nics = await idx.nic_networks()
    assert nics[("default", "podman-1", "enp5s0")] == "legacy-infra"
    assert route.call_count == 1
    now[0] = 61
    await idx.get()
    assert route.call_count == 2
    idx.invalidate()
    await idx.get()
    assert route.call_count == 3
