"""Local storage pools against fixtures recorded from a real LXD (zfs, btrfs, lvm, dir)."""

import json
from pathlib import Path

import pytest
import respx

from mcui.config import Settings
from mcui.main import create_app
from mcui.metrics.live import instance_totals
from mcui.metrics.prom import parse
from mcui.metrics.select import member_state_samples
from mcui.services import build_services
from tests.conftest import EP1, fast_store, lxd_ok
from tests.test_api import client, login, lxd_pair

DIR = Path(__file__).parent / "fixtures" / "recorded-local"
MIB16 = 16 * 1024 * 1024


def doc(name: str):
    return json.loads((DIR / name).read_text())["metadata"]


def test_container_disk_io_counted_once_per_pool_type():
    totals = instance_totals(parse((DIR / "metrics_tl1.txt").read_text(), strict=True))
    written = {name: t.values.get("disk_written", 0) for (_, name), t in totals.items()}
    # lvm: the same writes appear on dm-1, dm-2, dm-4 and the disk; counted once
    assert MIB16 <= written["c-ll"] < 2 * MIB16
    assert MIB16 <= written["c-lb"] < 2 * MIB16  # btrfs on a loop: charged to the disk
    assert MIB16 <= written["c-local-dir"] < 2 * MIB16
    assert written["c-lz"] == 0  # ZFS doesn't attribute I/O to containers
    for name in ("c-ll", "c-lb", "c-local-dir", "c-lz"):
        t = totals[("default", name)]
        assert t.values.get("rootfs_size"), f"{name}: no root filesystem size"


def test_member_state_has_every_local_pool():
    samples = member_state_samples("tl1", doc("1.0_cluster_members_tl1_state.json"))
    used = {s.labels["pool"]: s.value for s in samples if s.name == "mcui_pool_space_used"}
    total = {s.labels["pool"]: s.value for s in samples if s.name == "mcui_pool_space_total"}
    assert set(total) == {"lb", "ll", "local-dir", "lz"}
    assert all(0 < used[p] < total[p] for p in total)
    inodes = {s.labels["pool"] for s in samples if s.name == "mcui_pool_inodes_total"}
    assert inodes == {"local-dir"}  # only dir reports inodes


@pytest.fixture
def app(tmp_path):
    store = fast_store()
    store.add_user("viewer", "viewerpw", "viewer")
    settings = Settings(server={"data_dir": str(tmp_path), "secure_cookies": False})
    return create_app(
        services=build_services(settings, store=store, clients=lxd_pair()), static_dir=None
    )


@respx.mock
async def test_pool_members_and_volumes_from_fixtures(app):
    respx.get(f"{EP1}/1.0").mock(return_value=lxd_ok(doc("1.0.json")))
    respx.get(f"{EP1}/1.0/cluster/members", params={"recursion": "1"}).mock(
        return_value=lxd_ok(doc("1.0_cluster_members_recursion_1.json"))
    )
    pools = ("lb", "ll", "local-dir", "lz")
    for p in pools:
        respx.get(f"{EP1}/1.0/storage-pools/{p}", params={"target": "tl1"}).mock(
            return_value=lxd_ok(doc(f"1.0_storage-pools_{p}_target_tl1.json"))
        )
        respx.get(f"{EP1}/1.0/storage-pools/{p}/resources", params={"target": "tl1"}).mock(
            return_value=lxd_ok(doc(f"1.0_storage-pools_{p}_resources_target_tl1.json"))
        )
        respx.get(f"{EP1}/1.0/storage-pools/{p}/volumes").mock(
            return_value=lxd_ok(
                doc(f"1.0_storage-pools_{p}_volumes_recursion_1_all-projects_true.json")
            )
        )
    respx.get(f"{EP1}/1.0/storage-pools").mock(
        return_value=lxd_ok(doc("1.0_storage-pools_recursion_1.json"))
    )
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        [lz] = (await c.get("/api/storage-pools/lz/members")).json()
        assert lz["member"] == "tl1" and lz["status"] == "Created"
        assert lz["config"]["zfs.pool_name"] == "lz" and lz["config"]["source"].endswith("lz.img")
        assert lz["resources"]["space"]["total"] > lz["resources"]["space"]["used"] > 0
        [ll] = (await c.get("/api/storage-pools/ll/members")).json()
        assert ll["config"]["lvm.vg_name"] == "ll" and ll["config"]["lvm.thinpool_name"]
        vols = (await c.get("/api/volumes")).json()
        assert sorted((v["pool"], v["name"], v["content_type"], v["location"]) for v in vols) == [
            ("ll", "vb", "block", "tl1"),
            ("lz", "v1", "filesystem", "tl1"),
        ]
        listed = (await c.get("/api/storage-pools")).json()
        assert {p["name"]: p["driver"] for p in listed} == {
            "lb": "btrfs",
            "ll": "lvm",
            "local-dir": "dir",
            "lz": "zfs",
        }
