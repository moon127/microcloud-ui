"""The folder browser's helpers (D87). LXD's answers as seen live on LXD 6.10.1."""

import asyncio
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
OURS = {
    "name": "mcui-server",
    "config": {"user.mcui.role": "host-browser", "features.images": "true"},
}
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


NOW = 1_795_000_000.0  # the wall clock: 2026-11-18
# a helper made yesterday from the configured image, as LXD lists it
FRESH = {"user.mcui.image": "alpine/3.24", "volatile.base_image": "aaa"}
MADE = "2026-11-17T09:00:00.123456789Z"


@pytest.fixture
def lxd():
    return LXDClient([EP1], verify=ssl.create_default_context())


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def browser(lxd, clock):
    return HostBrowser(lxd, HostDevicesSettings(browse_idle_minutes=5), clock, lambda: NOW)


def no_images():
    """The pruning after a helper is made: nothing to prune."""
    respx.get(f"{EP1}/1.0/instances", params={"recursion": "1"}).mock(return_value=lxd_ok([]))
    respx.get(f"{EP1}/1.0/images").mock(return_value=lxd_ok([]))


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
    assert helper_name("node-1") == "browse-node-1"
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
    helper = {
        "name": "browse-node-1",
        "location": "node-1",
        "status": "Stopped",
        "created_at": MADE,
        "config": {
            **FRESH,
            "image.description": "Alpine 3.24 (default)",
            "image.serial": "20261117_0052",
        },
    }
    respx.get(f"{EP1}/1.0/instances").mock(return_value=lxd_ok([helper]))
    st = await browser.status()
    assert st["helpers"] == [
        {
            "name": "browse-node-1",
            "member": "node-1",
            "status": "Stopped",
            "image": "Alpine 3.24 (default), build 20261117_0052",
            "created_at": 1794906000.123456,
            "stale": False,
        }
    ]
    assert st["image"] == "alpine/3.24" and st["refresh_days"] == 7
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
    no_images()
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
    assert body["source"] == {"type": "image", "alias": "alpine/3.24", "image_registry": "images"}
    assert body["config"]["user.mcui.image"] == "alpine/3.24"
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
    no_images()
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
    stopped = {"status": "Stopped", "devices": have, "config": FRESH, "created_at": MADE}
    respx.get(H).mock(return_value=lxd_ok(stopped))
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
                "required": "false",
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
    respx.get(P).mock(return_value=lxd_err("Project not found", 404))  # nothing else running
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
            [
                {"name": "browse-node-1", "status": "Running"},
                {"name": "browse-node-2", "status": "Stopped"},
            ]
        )
    )
    stop = respx.put(f"{H}/state").mock(return_value=async_resp())
    d1 = respx.delete(H).mock(return_value=async_resp())
    d2 = respx.delete(f"{EP1}/1.0/instances/browse-node-2").mock(return_value=async_resp())
    respx.get(f"{EP1}/1.0/images").mock(return_value=lxd_ok([{"fingerprint": "abc"}]))
    di = respx.delete(f"{EP1}/1.0/images/abc").mock(return_value=async_resp())
    dp = respx.delete(P).mock(return_value=lxd_ok(None))
    wait_with()
    browser.last_used = {"node-1": 1.0}
    assert await browser.remove_all() is True
    assert stop.called and d1.called and d2.called and di.called and dp.called
    assert dict(di.calls.last.request.url.params) == {"project": "mcui-server"}
    assert browser.last_used == {}


@respx.mock
async def test_helper_output_is_capped(browser, monkeypatch):
    """A folder with millions of entries mustn't fill the server's memory (D89)."""
    monkeypatch.setattr("mcui.lxd.hostbrowser.MAX_OUTPUT", 40)
    respx.get(P).mock(return_value=lxd_ok(OURS))
    respx.get(H).mock(return_value=lxd_ok({"status": "Running"}))
    exec_mocks("directory/1/1//host/aaaa\n" * 10)
    entries = await browser.listing("node-1", "/", [])
    assert [e.name for e in entries] == ["aaaa"]  # one whole line in 40 bytes; the cut one dropped


@respx.mock
async def test_resolve(browser):
    """Links resolved in the helper (D90); one leaving the member's folders is refused."""
    respx.get(P).mock(return_value=lxd_ok(OURS))
    respx.get(H).mock(return_value=lxd_ok({"status": "Running", "devices": {}}))
    exec_mocks("/host/etc\n")
    assert await browser.resolve("node-1", "/srv/nfs/a", []) == "/etc"
    exec_mocks("/host\n")
    assert await browser.resolve("node-1", "/", []) == "/"
    exec_mocks("/etc/passwd\n")  # an absolute link: resolved inside the helper
    with pytest.raises(BrowseError, match="link out of the host's folders") as e:
        await browser.resolve("node-1", "/srv/x", [])
    assert e.value.status == 403
    exec_mocks("", "realpath: /host/nope: No such file\n", rc=1)
    with pytest.raises(BrowseError, match="doesn't exist"):
        await browser.resolve("node-1", "/nope", [])


@respx.mock
async def test_stop_idle_spares_a_helper_used_meanwhile(browser, clock):
    respx.get(P).mock(return_value=lxd_ok(OURS))
    running = {"name": "browse-node-1", "location": "node-1", "status": "Running", "config": FRESH}
    respx.get(f"{EP1}/1.0/instances").mock(return_value=lxd_ok([running]))
    browser.last_used = {"node-1": 1000.0}
    clock.t = 1000.0 + 301
    stop = respx.put(f"{H}/state").mock(return_value=async_resp())
    wait_with()
    await browser._lock.acquire()
    task = asyncio.create_task(browser.stop_idle())
    await asyncio.sleep(0)
    browser.last_used["node-1"] = clock.t  # a listing started while stop_idle waited
    browser._lock.release()
    assert await task == [] and not stop.called


# --- keeping helpers current (D97) ------------------------------------------------------


@respx.mock
async def test_old_or_other_image_helpers_are_made_again(browser):
    """A stopped helper past browse_refresh_days, or made from another image, is deleted
    and made again from the current image; images no helper uses go."""
    respx.get(P).mock(return_value=lxd_ok(OURS))
    respx.get(f"{EP1}/1.0").mock(return_value=lxd_ok({"api_extensions": ["image_registries"]}))
    old = {
        "status": "Stopped",
        "devices": {},
        "config": FRESH,
        "created_at": "2026-11-01T00:00:00Z",
    }
    respx.get(H).mock(return_value=lxd_ok(old))
    delete = respx.delete(H).mock(return_value=async_resp())
    create = respx.post(f"{EP1}/1.0/instances").mock(return_value=async_resp())
    new = {"name": "browse-node-1", "config": {**FRESH, "volatile.base_image": "bbb"}}
    respx.get(f"{EP1}/1.0/instances", params={"recursion": "1"}).mock(return_value=lxd_ok([new]))
    respx.get(f"{EP1}/1.0/images").mock(
        return_value=lxd_ok([{"fingerprint": "aaa"}, {"fingerprint": "bbb"}])
    )
    gone = respx.delete(f"{EP1}/1.0/images/aaa").mock(return_value=async_resp())
    exec_mocks("")
    await browser.listing("node-1", "/srv", [])
    assert delete.called and create.called and gone.called
    # made from another image (browse_image changed): made again too
    for inst, again in [
        ({**old, "created_at": MADE, "config": {"user.mcui.image": "alpine/edge"}}, True),
        ({**old, "created_at": MADE}, False),  # recent and current: kept
        ({**old, "created_at": "not a date"}, True),
        ({**old, "status": "Running"}, False),  # in use: left alone until it stops
    ]:
        delete.reset()
        respx.get(H).mock(return_value=lxd_ok(inst))
        respx.put(f"{H}/state").mock(return_value=async_resp())
        await browser.listing("node-1", "/srv", [])
        assert delete.called is again, inst


@respx.mock
async def test_prune_keeps_going_past_an_image_in_use(browser):
    respx.get(P).mock(return_value=lxd_ok(OURS))
    respx.get(f"{EP1}/1.0/instances").mock(return_value=lxd_ok([]))
    respx.get(f"{EP1}/1.0/images").mock(
        return_value=lxd_ok([{"fingerprint": "aaa"}, {"fingerprint": "bbb"}])
    )
    respx.delete(f"{EP1}/1.0/images/aaa").mock(return_value=lxd_err("in use", 400))
    respx.delete(f"{EP1}/1.0/images/bbb").mock(return_value=async_resp())
    wait_with()
    assert await browser._prune() == ["bbb"]
    # a project without images of its own would list the default project's: left alone
    respx.get(P).mock(return_value=lxd_ok({**OURS, "config": {"user.mcui.role": "host-browser"}}))
    assert await browser._prune() == []


@respx.mock
async def test_rebuild_all(browser):
    respx.get(P).mock(return_value=lxd_err("Project not found", 404))
    assert await browser.rebuild_all() == (0, [])
    respx.get(P).mock(return_value=lxd_ok({"name": "mcui-server", "config": {}}))
    assert await browser.rebuild_all() == (0, [])  # not the app's: left alone
    respx.get(P).mock(return_value=lxd_ok(OURS))
    running = {"name": "browse-node-1", "location": "node-1", "status": "Running"}
    frozen = {"name": "browse-node-2", "location": "node-2", "status": "Frozen"}
    gone = {"name": "browse-node-3", "location": "node-3", "status": "Stopped"}
    busy = {"name": "browse-node-4", "location": "node-4", "status": "Running"}
    insts = respx.get(f"{EP1}/1.0/instances")
    insts.side_effect = [lxd_ok([running, frozen, gone, busy]), lxd_ok([busy])]
    respx.get(f"{EP1}/1.0/images").mock(return_value=lxd_ok([{"fingerprint": "aaa"}]))
    stop = respx.put(f"{H}/state").mock(return_value=async_resp())
    stop2 = respx.put(f"{EP1}/1.0/instances/browse-node-2/state").mock(return_value=async_resp())
    d1 = respx.delete(H).mock(return_value=async_resp())
    d2 = respx.delete(f"{EP1}/1.0/instances/browse-node-2").mock(return_value=async_resp())
    # a member that refuses: the rest still go (D97 review)
    respx.delete(f"{EP1}/1.0/instances/browse-node-3").mock(return_value=lxd_err("offline", 503))
    d4 = respx.delete(f"{EP1}/1.0/instances/browse-node-4").mock(return_value=async_resp())
    img = respx.delete(f"{EP1}/1.0/images/aaa").mock(return_value=async_resp())
    wait_with()
    browser.last_used = {"node-1": 1.0, "node-4": 2.0}
    browser.in_use = {"node-4": 1}  # being browsed right now: made again once it stops
    done, failed = await browser.rebuild_all()
    assert done == 3 and len(failed) == 1 and "browse-node-3" in failed[0]
    assert stop.called and d1.called and d2.called and img.called
    assert stop2.called  # frozen: stopped first, LXD won't delete it otherwise
    assert not d4.called and browser.remake == {"node-4"}
    assert browser.last_used == {"node-4": 2.0}


@respx.mock
async def test_remake_falls_back_to_the_old_image(browser):
    """The image server unreachable while a helper is made again: made from the image the
    old one was made from (still in the project), and nothing pruned (D97 review)."""
    respx.get(P).mock(return_value=lxd_ok(OURS))
    respx.get(f"{EP1}/1.0").mock(return_value=lxd_ok({"api_extensions": ["image_registries"]}))
    old = {
        "status": "Stopped",
        "devices": {},
        "config": FRESH,
        "created_at": "2026-11-01T00:00:00Z",
    }
    respx.get(H).mock(return_value=lxd_ok(old))
    respx.delete(H).mock(return_value=async_resp())
    create = respx.post(f"{EP1}/1.0/instances")
    create.side_effect = [lxd_err("Failed getting remote image info", 500), async_resp()]
    images = respx.get(f"{EP1}/1.0/images")
    exec_mocks("")
    await browser.listing("node-1", "/srv", [])
    again = json.loads(create.calls.last.request.content)
    assert again["source"] == {"type": "image", "fingerprint": "aaa"}
    assert again["config"]["user.mcui.image"] == ""  # stale: tried from the server next time
    assert not images.called
    assert browser.in_use == {}  # the listing's over
    # a first helper (nothing to fall back to): the error stands
    respx.get(H).mock(return_value=lxd_err("Instance not found", 404))
    create.side_effect = [lxd_err("Failed getting remote image info", 500)]
    with pytest.raises(LXDError):
        await browser.listing("node-1", "/srv", [])


@respx.mock
async def test_prune_failing_after_a_create_doesnt_fail_the_listing(browser):
    respx.get(P).mock(return_value=lxd_ok(OURS))
    respx.get(f"{EP1}/1.0").mock(return_value=lxd_ok({"api_extensions": ["image_registries"]}))
    respx.get(H).mock(return_value=lxd_err("Instance not found", 404))
    respx.post(f"{EP1}/1.0/instances").mock(return_value=async_resp())
    respx.get(f"{EP1}/1.0/instances").mock(return_value=lxd_err("boom", 500))
    exec_mocks("directory/4096/1//host/srv/x\n")
    assert [e.name for e in await browser.listing("node-1", "/srv", [])] == ["x"]


@respx.mock
async def test_frozen_helper_unfrozen(browser):
    respx.get(P).mock(return_value=lxd_ok(OURS))
    frozen = {
        "status": "Frozen",
        "devices": {},
        "config": FRESH,
        "created_at": "2026-11-01T00:00:00Z",
    }
    respx.get(H).mock(return_value=lxd_ok(frozen))
    state = respx.put(f"{H}/state").mock(return_value=async_resp())
    delete = respx.delete(H)
    exec_mocks("")
    await browser.listing("node-1", "/srv", [])
    assert json.loads(state.calls.last.request.content) == {"action": "unfreeze"}
    assert not delete.called  # only a stopped helper is made again


@respx.mock
async def test_a_marked_helper_is_made_again_once_stopped(browser):
    respx.get(P).mock(return_value=lxd_ok(OURS))
    respx.get(f"{EP1}/1.0").mock(return_value=lxd_ok({"api_extensions": ["image_registries"]}))
    fresh = {"status": "Stopped", "devices": {}, "config": FRESH, "created_at": MADE}
    respx.get(H).mock(return_value=lxd_ok(fresh))
    delete = respx.delete(H).mock(return_value=async_resp())
    respx.post(f"{EP1}/1.0/instances").mock(return_value=async_resp())
    no_images()
    exec_mocks("")
    browser.remake.add("node-1")
    await browser.listing("node-1", "/srv", [])
    assert delete.called and browser.remake == set()


@respx.mock
async def test_helpers_left_running_across_a_restart_are_stopped(browser):
    """stop_idle also stops helpers the app isn't timing (it restarted meanwhile); a stale
    one is deleted, to be made again on its next use (D97 review)."""
    respx.get(P).mock(return_value=lxd_ok(OURS))
    fresh = {
        "name": "browse-node-1",
        "location": "node-1",
        "status": "Running",
        "config": FRESH,
        "created_at": MADE,
    }
    stale = {"name": "browse-node-2", "location": "node-2", "status": "Running", "config": {}}
    timed = {"name": "browse-node-3", "location": "node-3", "status": "Running", "config": FRESH}
    down = {"name": "browse-node-4", "location": "node-4", "status": "Stopped"}
    broken = {
        "name": "browse-node-5",
        "location": "node-5",
        "status": "Running",
        "config": FRESH,
        "created_at": MADE,
    }
    respx.get(f"{EP1}/1.0/instances").mock(return_value=lxd_ok([fresh, stale, timed, down, broken]))
    stop1 = respx.put(f"{H}/state").mock(return_value=async_resp())
    stop2 = respx.put(f"{EP1}/1.0/instances/browse-node-2/state").mock(return_value=async_resp())
    del2 = respx.delete(f"{EP1}/1.0/instances/browse-node-2").mock(return_value=async_resp())
    respx.put(f"{EP1}/1.0/instances/browse-node-5/state").mock(return_value=lxd_err("x", 500))
    wait_with()
    browser.last_used = {"node-3": 1000.0}  # still timed: left to the idle rule
    assert await browser.stop_idle() == ["node-1", "node-2"]
    assert stop1.called and stop2.called and del2.called


@respx.mock
async def test_image_warning(lxd, clock):
    respx.get(P).mock(return_value=lxd_ok(OURS))
    respx.get(f"{EP1}/1.0/instances").mock(return_value=lxd_ok([]))
    for image, warning in [
        ("alpine/3.24", None),
        ("alpine/edge", "isn't a numbered Alpine release"),
        ("alpine/3.23", "isn't the default (alpine/3.24)"),
    ]:
        b = HostBrowser(lxd, HostDevicesSettings(browse_image=image), clock)
        got = (await b.status())["image_warning"]
        assert (got is None) if warning is None else (warning in got), image
