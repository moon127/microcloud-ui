"""The folder browser's helpers (D87). LXD's answers as seen live on LXD 6.10.1."""

import json
import ssl

import httpx
import pytest
import respx

from mcui.config import HostDevicesSettings
from mcui.lxd.client import LXDClient, LXDError
from mcui.lxd.hostbrowser import (
    BrowseError,
    HostBrowser,
    helper_name,
    mount_device,
    parse_listing,
)
from tests.conftest import EP1, lxd_err, lxd_ok

P = f"{EP1}/1.0/projects/mcui-server"
H = f"{EP1}/1.0/instances/browse-node-1"
OURS = {"name": "mcui-server", "config": {"user.mcui.role": "host-browser"}}
# as LXD 6.10.1's helper printed it for the member's /
LISTING = """directory/4096/1754412882//host/usr
symbolic link/7/1713791283//host/lib
regular file/8589934592/1759073427//host/swap.img
regular empty file/0/1759073427//host/.autorelabel
character special file/0/1759073427//host/null
directory/4096/1759073734//host/home
broken line
"""


def op(metadata=None, status="Success", err=""):
    return {"id": "op1", "status": status, "err": err, "metadata": metadata}


def async_resp():
    return httpx.Response(202, json={"type": "async", "metadata": op(status="Running")})


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def lxd():
    return LXDClient([EP1], verify=ssl.create_default_context())


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def browser(lxd, clock):
    return HostBrowser(lxd, HostDevicesSettings(browse_idle_minutes=5), clock)


def wait_with(metadata=None, status="Success", err=""):
    return respx.get(f"{EP1}/1.0/operations/op1/wait").mock(
        return_value=lxd_ok(op(metadata, status, err))
    )


def exec_mocks(out="", err="", rc=0):
    respx.post(f"{H}/exec").mock(return_value=async_resp())
    output = {"1": "/1.0/instances/browse-node-1/logs/exec-output/o.stdout"}
    if err:
        output["2"] = "/1.0/instances/browse-node-1/logs/exec-output/o.stderr"
    wait_with({"output": output, "return": rc})
    logs = f"{H}/logs/exec-output"
    respx.get(f"{logs}/o.stdout").mock(return_value=httpx.Response(200, text=out))
    respx.get(f"{logs}/o.stderr").mock(return_value=httpx.Response(200, text=err))
    return respx.delete(url__startswith=f"{logs}/").mock(return_value=lxd_ok(None))


def test_names_and_parsing():
    assert helper_name("gw-ips-microcloud-1") == "browse-gw-ips-microcloud-1"
    assert helper_name("Node_1.lan") == "browse-node-1-lan"
    assert len(helper_name("x" * 80)) == 63
    assert mount_device("/srv/nfs") == mount_device("/srv/nfs") != mount_device("/boot")
    entries = parse_listing(LISTING)
    assert [(e.name, e.type) for e in entries] == [
        ("home", "dir"),
        ("usr", "dir"),
        (".autorelabel", "file"),
        ("lib", "link"),
        ("null", "other"),
        ("swap.img", "file"),
    ]
    assert entries[-1].size == 8589934592 and entries[-1].mtime == 1759073427


@respx.mock
async def test_status(browser):
    respx.get(P).mock(return_value=lxd_err("Project not found", 404))
    assert await browser.status() == {
        "project": "mcui-server",
        "exists": False,
        "ours": False,
        "helpers": [],
    }
    respx.get(P).mock(return_value=lxd_ok({"name": "mcui-server", "config": {}}))
    assert (await browser.status())["ours"] is False
    respx.get(P).mock(return_value=lxd_ok(OURS))
    respx.get(f"{EP1}/1.0/instances").mock(
        return_value=lxd_ok([{"name": "browse-node-1", "location": "node-1", "status": "Stopped"}])
    )
    assert (await browser.status())["helpers"] == [
        {"name": "browse-node-1", "member": "node-1", "status": "Stopped"}
    ]
    respx.get(P).mock(return_value=lxd_err("boom", 500))
    with pytest.raises(LXDError):
        await browser.status()


@respx.mock
async def test_first_listing_creates_the_project_and_helper(browser):
    respx.get(P).mock(return_value=lxd_err("Project not found", 404))
    create_p = respx.post(f"{EP1}/1.0/projects").mock(return_value=lxd_ok(None))
    respx.get(f"{EP1}/1.0/profiles/default", params={"project": "default"}).mock(
        return_value=lxd_ok({"devices": {"root": {"pool": "remote"}}})
    )
    profile = respx.put(f"{EP1}/1.0/profiles/default").mock(return_value=lxd_ok(None))
    respx.get(f"{EP1}/1.0").mock(return_value=lxd_ok({"api_extensions": ["image_registries"]}))
    respx.get(H).mock(return_value=lxd_err("Instance not found", 404))
    create_i = respx.post(f"{EP1}/1.0/instances").mock(return_value=async_resp())
    delete = exec_mocks(LISTING)
    entries = await browser.listing("node-1", "/", [])
    assert [e.name for e in entries][:2] == ["home", "usr"]
    sent = json.loads(create_p.calls.last.request.content)
    assert sent["name"] == "mcui-server"
    assert sent["config"]["user.mcui.role"] == "host-browser"
    assert json.loads(profile.calls.last.request.content)["devices"]["root"] == {
        "type": "disk",
        "path": "/",
        "pool": "remote",
        "size": "1GiB",
    }
    body = json.loads(create_i.calls.last.request.content)
    assert dict(create_i.calls.last.request.url.params) == {
        "project": "mcui-server",
        "target": "node-1",
    }
    assert body["source"] == {"type": "image", "alias": "alpine/edge", "image_registry": "images"}
    assert body["devices"] == {
        "host": {"type": "disk", "source": "/", "path": "/host", "readonly": "true"}
    }
    assert body["config"]["cluster.evacuate"] == "stop" and body["start"] is True
    ex = json.loads(respx.calls[-4].request.content)
    assert ex["command"][:2] == ["find", "/host/"]
    assert delete.called  # the recorded output isn't left behind
    assert browser.last_used == {"node-1": 1000.0}


@respx.mock
async def test_helper_on_lxd_5_and_a_configured_pool(lxd, clock):
    b = HostBrowser(lxd, HostDevicesSettings(browse_pool="local"), clock)
    respx.get(P).mock(return_value=lxd_err("Project not found", 404))
    respx.post(f"{EP1}/1.0/projects").mock(return_value=lxd_ok(None))
    profile = respx.put(f"{EP1}/1.0/profiles/default").mock(return_value=lxd_ok(None))
    respx.get(f"{EP1}/1.0").mock(return_value=lxd_ok({"api_extensions": []}))
    respx.get(H).mock(return_value=lxd_err("Instance not found", 404))
    create_i = respx.post(f"{EP1}/1.0/instances").mock(return_value=async_resp())
    exec_mocks("")
    assert await b.listing("node-1", "/srv", []) == []
    assert json.loads(profile.calls.last.request.content)["devices"]["root"]["pool"] == "local"
    assert json.loads(create_i.calls.last.request.content)["source"]["protocol"] == "simplestreams"


@respx.mock
async def test_no_pool_and_someone_elses_project(browser):
    respx.get(P).mock(return_value=lxd_err("Project not found", 404))
    respx.post(f"{EP1}/1.0/projects").mock(return_value=lxd_ok(None))
    respx.get(f"{EP1}/1.0/profiles/default").mock(return_value=lxd_ok({"devices": {}}))
    with pytest.raises(BrowseError, match="set browse_pool"):
        await browser.prepare()
    respx.get(P).mock(return_value=lxd_ok({"name": "mcui-server", "config": {}}))
    with pytest.raises(BrowseError, match="isn't the app's") as e:
        await browser.prepare()
    assert e.value.status == 409
    with pytest.raises(BrowseError, match="not deleted"):
        await browser.remove_all()


@respx.mock
async def test_starts_a_stopped_helper_and_mounts_filesystems_on_the_way(browser):
    respx.get(P).mock(return_value=lxd_ok(OURS))
    have = {"host": {}, mount_device("/srv/nfs"): {}}
    respx.get(H).mock(return_value=lxd_ok({"status": "Stopped", "devices": have}))
    start = respx.put(f"{H}/state").mock(return_value=async_resp())
    patch = respx.patch(H).mock(return_value=async_resp())
    exec_mocks("directory/4096/1//host/srv/nfs/deep/x\n")
    mounts = ["/", "/boot", "/srv/nfs", "/srv/nfs/deep", "/srv/nfsx"]
    entries = await browser.listing("node-1", "/srv/nfs/deep", mounts)
    assert [e.name for e in entries] == ["x"]
    assert json.loads(start.calls.last.request.content) == {"action": "start"}
    # /srv/nfs is there already; /boot and /srv/nfsx aren't on the way
    assert json.loads(patch.calls.last.request.content) == {
        "devices": {
            mount_device("/srv/nfs/deep"): {
                "type": "disk",
                "source": "/srv/nfs/deep",
                "path": "/host/srv/nfs/deep",
                "readonly": "true",
            }
        }
    }
    # all there: nothing added
    respx.get(H).mock(return_value=lxd_ok({"status": "Running", "devices": have}))
    await browser.listing("node-1", "/srv/nfs", mounts)
    assert patch.call_count == 1


@pytest.mark.parametrize(
    ("err", "status", "match"),
    [
        ("find: /host/root: Permission denied\n", 403, "isn't readable by ordinary users"),
        ("find: /host/x: No such file or directory\n", 404, "doesn't exist"),
        ("find: odd\n", 502, "failed: find: odd"),
    ],
)
@respx.mock
async def test_listing_errors(browser, err, status, match):
    respx.get(P).mock(return_value=lxd_ok(OURS))
    respx.get(H).mock(return_value=lxd_ok({"status": "Running", "devices": {}}))
    exec_mocks("", err, rc=1)
    with pytest.raises(BrowseError, match=match) as e:
        await browser.listing("node-1", "/x", [])
    assert e.value.status == status


@respx.mock
async def test_some_of_a_folder_unreadable_still_lists(browser):
    respx.get(P).mock(return_value=lxd_ok(OURS))
    respx.get(H).mock(return_value=lxd_ok({"status": "Running"}))
    exec_mocks("directory/1/1//host/a\n", "find: /host/b: Permission denied\n", rc=1)
    assert [e.name for e in await browser.listing("node-1", "/", [])] == ["a"]


@respx.mock
async def test_other_lxd_errors_on_the_helper(browser):
    respx.get(P).mock(return_value=lxd_ok(OURS))
    respx.get(H).mock(return_value=lxd_err("boom", 500))
    with pytest.raises(LXDError):
        await browser.listing("node-1", "/", [])


@respx.mock
async def test_stop_idle(browser, clock):
    browser.last_used = {"node-1": 1000.0, "node-2": 1200.0}
    stop = respx.put(f"{H}/state").mock(return_value=async_resp())
    respx.put(f"{EP1}/1.0/instances/browse-node-2/state").mock(return_value=lxd_err("x", 500))
    wait_with()
    clock.t = 1000.0 + 299
    assert await browser.stop_idle() == []
    clock.t = 1000.0 + 300
    assert await browser.stop_idle() == ["node-1"]
    assert json.loads(stop.calls.last.request.content) == {"action": "stop", "force": True}
    clock.t = 1200.0 + 300
    assert await browser.stop_idle() == []  # failed: logged, forgotten
    assert browser.last_used == {}


@respx.mock
async def test_remove_all(browser):
    respx.get(P).mock(return_value=lxd_err("Project not found", 404))
    assert await browser.remove_all() is False
    respx.get(P).mock(return_value=lxd_ok(OURS))
    respx.get(f"{EP1}/1.0/instances").mock(
        return_value=lxd_ok(
            [{"name": "browse-node-1", "status": "Running"}, {"name": "browse-node-2"}]
        )
    )
    stop = respx.put(f"{H}/state").mock(return_value=async_resp())
    d1 = respx.delete(H).mock(return_value=async_resp())
    d2 = respx.delete(f"{EP1}/1.0/instances/browse-node-2").mock(return_value=async_resp())
    respx.get(f"{EP1}/1.0/images").mock(return_value=lxd_ok(["/1.0/images/abc"]))
    di = respx.delete(f"{EP1}/1.0/images/abc").mock(return_value=async_resp())
    dp = respx.delete(P).mock(return_value=lxd_ok(None))
    wait_with()
    browser.last_used = {"node-1": 1.0}
    assert await browser.remove_all() is True
    assert stop.called and d1.called and d2.called and di.called and dp.called
    assert dict(di.calls.last.request.url.params) == {"project": "mcui-server"}
    assert browser.last_used == {}
