"""Host paths and USB devices (D86). LXD's answers as seen live on LXD 6.10 in mcui-test-2."""

import json
from pathlib import Path

import httpx
import pytest
import respx

from mcui.auth.policy import host_device_changes, is_host_device, may_use_host_devices
from mcui.auth.store import User
from mcui.config import Settings
from mcui.main import create_app
from mcui.services import build_services
from tests.conftest import EP1, fast_store, lxd_err, lxd_ok
from tests.test_api import CSRF_HEADER, client, login, lxd_pair
from tests.test_api_instances import admin, app, async_resp, last_audit, services

__all__ = ["app", "services"]  # fixtures

INST = f"{EP1}/1.0/instances/c1"
U = "/api/instances/default/c1/host-devices"
RECORDED = Path(__file__).parent / "fixtures/recorded/1.0_resources_target_gw-ips-microcloud-1.json"
DOC = {"type": "disk", "source": "/srv/nfs", "path": "/mnt/nfs", "readonly": "true"}
ROOT = {"type": "disk", "path": "/", "pool": "remote"}


def resources():
    data = json.loads(RECORDED.read_text())
    res = data.get("metadata", data)
    stick = {
        "id": "sdc",
        "type": "usb",
        "model": "Flash Disk",
        "size": 16 * 2**30,
        "device_id": "usb-Generic_Flash_Disk_0123-0:0",
        "partitions": [{"id": "sdc1", "partition": 1, "size": 16 * 2**30}, {"id": "x"}],
    }
    res["storage"]["disks"].append(stick)
    res["storage"]["disks"].append({"id": "sdd", "type": "usb", "partitions": None})
    res["usb"]["devices"].append(
        {
            "vendor_id": "0781",
            "product_id": "5581",
            "vendor": "SanDisk",
            "product": "Ultra",
            "serial": "4C53",
            "bus_address": 2,
            "device_address": 3,
            "interfaces": [{"class_id": 8, "driver": "usb-storage"}, {"class_id": 8}],
        }
    )
    return res


def test_policy():
    a, v = User(1, "a", "admin"), User(2, "v", "viewer")
    pat = User(3, "p", "project", {"web": "admin", "lab": "admin"}, frozenset({"web", "x"}))
    assert may_use_host_devices(a, "any") and not may_use_host_devices(v, "any")
    assert may_use_host_devices(pat, "web") and not may_use_host_devices(pat, "lab")
    assert not may_use_host_devices(pat, "x")  # no admin grant there
    assert is_host_device(DOC) and is_host_device({"type": "usb"})
    assert is_host_device({"type": "unix-char", "source": "/dev/kvm"})
    for dev in (
        ROOT,
        {"type": "disk", "pool": "remote", "source": "vol"},
        {"type": "disk", "source": "ceph:pool/vol"},
        {"type": "disk", "source": "cloud-init:config"},
        {"type": "nic", "network": "ovn"},
    ):
        assert not is_host_device(dev), dev
    old = {"root": ROOT, "doc": DOC}
    assert host_device_changes(old, old) == []
    assert host_device_changes({"root": ROOT}, old) == []  # removing is fine
    changed = {**old, "doc": {**DOC, "readonly": "false"}, "kvm": {"type": "usb"}}
    assert host_device_changes(changed, old) == ["doc", "kvm"]


@respx.mock
async def test_inventory(app):
    respx.get(INST).mock(return_value=lxd_ok({"name": "c1", "location": "m1"}))
    res = respx.get(f"{EP1}/1.0/resources").mock(return_value=lxd_ok(resources()))
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        assert (await c.get(U)).status_code == 403
        await admin(c)
        inv = (await c.get(U)).json()
    assert dict(res.calls.last.request.url.params) == {"target": "m1"}
    assert inv["member"] == "m1"
    # the BMC's virtual keyboard, as on the test cloud's members, and a USB stick
    assert inv["usb"] == [
        {
            "vendor_id": "0557",
            "product_id": "2419",
            "vendor": "ATEN International Co., Ltd",
            "product": "Virtual mouse/keyboard device",
            "serial": "",
            "busnum": 3,
            "devnum": 5,
            "storage": False,
            "drivers": ["usbhid"],
        },
        {
            "vendor_id": "0781",
            "product_id": "5581",
            "vendor": "SanDisk",
            "product": "Ultra",
            "serial": "4C53",
            "busnum": 2,
            "devnum": 3,
            "storage": True,
            "drivers": ["usb-storage"],
        },
    ]
    by_id = "/dev/disk/by-id/usb-Generic_Flash_Disk_0123-0:0"
    assert inv["disks"] == [
        {
            "id": "sdc",
            "model": "Flash Disk",
            "size": 16 * 2**30,
            "source": by_id,
            "partitions": [{"id": "sdc1", "size": 16 * 2**30, "source": f"{by_id}-part1"}],
        },
        {"id": "sdd", "model": "", "size": 0, "source": "/dev/sdd", "partitions": []},
    ]


@respx.mock
async def test_inventory_off_a_cluster(app):
    respx.get(INST).mock(return_value=lxd_ok({"name": "c1", "location": "none"}))
    res = respx.get(f"{EP1}/1.0/resources").mock(return_value=lxd_ok({}))
    async with client(app) as c:
        await admin(c)
        assert (await c.get(U)).json() == {"member": "", "usb": [], "disks": []}
    assert dict(res.calls.last.request.url.params) == {}


@respx.mock
async def test_add(app, services):
    respx.get(INST).mock(
        return_value=lxd_ok({"name": "c1", "type": "container", "expanded_devices": {"root": ROOT}})
    )
    patch = respx.patch(INST).mock(return_value=async_resp())
    path = {"kind": "path", "name": "nfs", "source": "/srv/nfs", "path": "/mnt/nfs"}
    async with client(app) as c:
        h = await admin(c)
        r = await c.post(U, json={**path, "everywhere": True, "shift": True}, headers=h)
        assert r.status_code == 200
        assert json.loads(patch.calls.last.request.content) == {
            "devices": {"nfs": {**DOC, "source": "/srv/nfs", "shift": "true"}},
            "config": {"cluster.evacuate": "migrate"},
        }
        assert last_audit(services)[0] == "instance.host-device.add"
        assert last_audit(services)[4]["cluster.evacuate"] == "migrate"
        await c.post(U, json={**path, "readonly": False}, headers=h)
        assert json.loads(patch.calls.last.request.content) == {
            "devices": {"nfs": {"type": "disk", "source": "/srv/nfs", "path": "/mnt/nfs"}}
        }
        usb = {"kind": "usb", "name": "stick", "vendorid": "0781", "productid": "55AB"}
        await c.post(U, json={**usb, "serial": "4C53", "busnum": 2, "devnum": 3}, headers=h)
        assert json.loads(patch.calls.last.request.content)["devices"] == {
            "stick": {
                "type": "usb",
                "vendorid": "0781",
                "productid": "55ab",
                "serial": "4C53",
                "busnum": "2",
                "devnum": "3",
            }
        }
        await c.post(U, json={**usb, "serial": "", "busnum": 2}, headers=h)
        assert json.loads(patch.calls.last.request.content)["devices"]["stick"] == {
            "type": "usb",
            "vendorid": "0781",
            "productid": "55ab",
        }
        r = await c.post(U, json={**path, "name": "root"}, headers=h)
        assert r.status_code == 409 and "already has a device root" in r.text
        r = await c.post(U, json={**path, "path": None}, headers=h)
        assert r.status_code == 400 and "needs a path" in r.text
        for bad in (
            {**path, "source": "srv"},
            {**path, "source": "/srv/../etc"},
            {**path, "path": "mnt"},
            {**usb, "vendorid": "xyz"},
            {**path, "kind": "pci"},
            {**path, "name": "../x"},
        ):
            assert (await c.post(U, json=bad, headers=h)).status_code == 422, bad
    assert patch.call_count == 4


@respx.mock
async def test_add_a_vm_block_device_without_a_path(app):
    respx.get(INST).mock(return_value=lxd_ok({"name": "c1", "type": "virtual-machine"}))
    patch = respx.patch(INST).mock(return_value=async_resp())
    disk = {"kind": "path", "name": "lun", "source": "/dev/disk/by-id/wwn-0x5", "readonly": False}
    async with client(app) as c:
        h = await admin(c)
        assert (await c.post(U, json=disk, headers=h)).status_code == 200
    assert json.loads(patch.calls.last.request.content)["devices"] == {
        "lun": {"type": "disk", "source": "/dev/disk/by-id/wwn-0x5"}
    }


@respx.mock
async def test_lxd_refusal_is_passed_on(app, services):
    """A restricted project without restricted.devices.disk=allow (seen live)."""
    respx.get(INST).mock(return_value=lxd_ok({"name": "c1", "type": "container"}))
    msg = "Attaching disks not backed by a pool is forbidden"
    respx.patch(INST).mock(return_value=lxd_err(msg, 403))
    body = {"kind": "path", "name": "nfs", "source": "/srv/nfs", "path": "/mnt/nfs"}
    async with client(app) as c:
        h = await admin(c)
        r = await c.post(U, json=body, headers=h)
    assert msg in r.text and last_audit(services)[2] is False


@respx.mock
async def test_remove(app, services):
    inst = {
        "name": "c1",
        "config": {"limits.cpu": "1"},
        "devices": {"nfs": DOC, "vol": {"type": "disk", "pool": "p", "source": "v"}},
        "profiles": ["default"],
        "description": "",
        "status": "Running",
    }
    respx.get(INST).mock(
        return_value=httpx.Response(
            200, json={"type": "sync", "metadata": inst}, headers={"ETag": "e1"}
        )
    )
    put = respx.put(INST).mock(return_value=async_resp())
    async with client(app) as c:
        h = await admin(c)
        r = await c.delete(f"{U}/nfs", headers=h)
        assert r.status_code == 200
        sent = json.loads(put.calls.last.request.content)
        assert sent["devices"] == {"vol": inst["devices"]["vol"]} and "status" not in sent
        assert put.calls.last.request.headers["if-match"] == "e1"
        assert last_audit(services)[:3] == ("instance.host-device.remove", "default/c1", True)
        for name in ("vol", "root"):
            r = await c.delete(f"{U}/{name}", headers=h)
            assert r.status_code == 409 and "not a host device" in r.text
        assert (await c.delete(f"{U}/-x", headers=h)).status_code == 422
    assert put.call_count == 1


@respx.mock
async def test_remove_without_an_etag_and_missing(app):
    respx.get(INST).mock(return_value=lxd_ok({"name": "c1", "devices": {"nfs": DOC}}))
    put = respx.put(INST).mock(return_value=async_resp())
    async with client(app) as c:
        h = await admin(c)
        assert (await c.delete(f"{U}/nfs", headers=h)).status_code == 200
        assert "if-match" not in put.calls.last.request.headers
        respx.get(INST).mock(return_value=lxd_err("Instance not found", 404))
        r = await c.delete(f"{U}/nfs", headers=h)
        assert r.status_code == 502 and "Instance not found" in r.text


@pytest.fixture
def scoped(tmp_path):
    """pat: admin in web and lab (both restricted), with the host-devices right in web."""
    store = fast_store()
    store.add_user("pat", "patpw", "project")
    store.set_grant("pat", "web", "admin", host_devices=True)
    store.set_grant("pat", "lab", "admin")
    settings = Settings(server={"data_dir": str(tmp_path), "secure_cookies": False})
    return create_app(
        services=build_services(settings, store=store, clients=lxd_pair()), static_dir=None
    )


def restricted():
    respx.get(url__regex=rf"{EP1}/1\.0/projects/(web|lab)$").mock(
        return_value=lxd_ok({"config": {"restricted": "true"}})
    )


@respx.mock
async def test_delegated_to_a_project_admin(scoped):
    restricted()
    respx.get(f"{EP1}/1.0/instances/c1").mock(
        return_value=lxd_ok({"name": "c1", "location": "m1", "type": "container"})
    )
    respx.get(f"{EP1}/1.0/resources").mock(return_value=lxd_ok({}))
    patch = respx.patch(f"{EP1}/1.0/instances/c1").mock(return_value=async_resp())
    usb = {"kind": "usb", "name": "k", "vendorid": "0557", "productid": "2419"}
    async with client(scoped) as c:
        h = {CSRF_HEADER: await login(c, "pat", "patpw")}
        assert (await c.get("/api/auth/me")).json()["user"]["host_devices"] == ["web"]
        assert (await c.get("/api/instances/web/c1/host-devices")).status_code == 200
        r = await c.post("/api/instances/web/c1/host-devices", json=usb, headers=h)
        assert r.status_code == 200 and patch.call_count == 1
        r = await c.get("/api/instances/lab/c1/host-devices")
        assert r.status_code == 403 and "host-devices right in lab" in r.text
        r = await c.post("/api/instances/lab/c1/host-devices", json=usb, headers=h)
        assert r.status_code == 403 and patch.call_count == 1
        r = await c.delete("/api/instances/lab/c1/host-devices/k", headers=h)
        assert r.status_code == 403
        r = await c.get("/api/instances/other/c1/host-devices")
        assert r.status_code == 404  # a project they can't see


@respx.mock
async def test_other_routes_guard_host_devices(scoped):
    """Without the right, a project admin can't add host devices through the YAML editor,
    the create wizard or a profile, but may keep ones a cluster admin added (seen live)."""
    restricted()
    current = {"name": "c1", "devices": {"nfs": DOC}}
    respx.get(f"{EP1}/1.0/instances/c1").mock(return_value=lxd_ok(current))
    respx.get(f"{EP1}/1.0/profiles/p1").mock(return_value=lxd_ok({"devices": {}}))
    put = respx.put(f"{EP1}/1.0/instances/c1").mock(return_value=async_resp())
    create = respx.post(f"{EP1}/1.0/instances").mock(return_value=async_resp())
    profile_put = respx.put(f"{EP1}/1.0/profiles/p1").mock(return_value=lxd_ok(None))
    profile_post = respx.post(f"{EP1}/1.0/profiles").mock(return_value=lxd_ok(None))
    usb = {"kvm": {"type": "usb", "vendorid": "0557"}}
    async with client(scoped) as c:
        h = {CSRF_HEADER: await login(c, "pat", "patpw")}
        upd = {"config": {}, "profiles": ["default"]}
        r = await c.put("/api/instances/lab/c1", json={**upd, "devices": {"nfs": DOC}}, headers=h)
        assert r.status_code == 200  # kept as it was
        r = await c.put("/api/instances/lab/c1", json={**upd, "devices": {}}, headers=h)
        assert r.status_code == 200  # removed
        r = await c.put(
            "/api/instances/lab/c1", json={**upd, "devices": {"nfs": DOC, **usb}}, headers=h
        )
        assert r.status_code == 403 and r.json()["detail"].startswith("kvm: host paths")
        assert put.call_count == 2
        # with the right in web
        r = await c.put("/api/instances/web/c1", json={**upd, "devices": usb}, headers=h)
        assert r.status_code == 200 and put.call_count == 3
        new = {"name": "c2", "image": {"remote": "local", "alias": "a"}, "devices": usb}
        r = await c.post("/api/instances", json={**new, "project": "lab"}, headers=h)
        assert r.status_code == 403 and not create.called
        r = await c.post("/api/instances", json={**new, "project": "web"}, headers=h)
        assert r.status_code == 200 and create.called
        r = await c.post(
            "/api/profiles", json={"name": "p2", "project": "lab", "devices": usb}, headers=h
        )
        assert r.status_code == 403 and not profile_post.called
        r = await c.put("/api/profiles/lab/p1", json={"devices": usb}, headers=h)
        assert r.status_code == 403 and not profile_put.called
        r = await c.put("/api/profiles/lab/p1", json={"devices": {"root": ROOT}}, headers=h)
        assert r.status_code == 200 and profile_put.called
