"""Host-device settings in the app (D87): hidden folders and the folder browser switch."""

import asyncio
import logging

import pytest
import respx

from mcui.config import Settings
from mcui.hostfs import BROWSE_KEY, DEFAULT_HIDDEN
from mcui.lxd.client import LXDError
from mcui.lxd.hostbrowser import BrowseError
from mcui.services import Services, build_services
from tests.conftest import fast_store
from tests.test_api import CSRF_HEADER, client, login, lxd_pair
from tests.test_api_instances import admin, app, last_audit, services

__all__ = ["app", "services"]  # fixtures

U = "/api/settings/host-devices"


class FakeBrowser:
    project = "mcui-server"

    def __init__(self):
        self.calls = []
        self.fail: Exception | None = None
        self.exists = False
        self.last_used = {}

    async def status(self):
        if isinstance(self.fail, RuntimeError):
            raise self.fail
        return {"project": self.project, "exists": self.exists, "ours": True, "helpers": []}

    async def prepare(self):
        self.calls.append("prepare")
        if self.fail:
            raise self.fail
        self.exists = True

    async def remove_all(self):
        self.calls.append("remove")
        if self.fail:
            raise self.fail
        gone, self.exists = self.exists, False
        return gone

    async def stop_idle(self):
        self.calls.append("stop_idle")
        if self.fail:
            raise self.fail
        return []


async def test_view_and_hidden_paths(app, services):
    services.browser = FakeBrowser()
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        assert (await c.get(U)).status_code == 403
        h = await admin(c)
        v = (await c.get(U)).json()
        assert v["hidden_paths"] == list(DEFAULT_HIDDEN) and v["hidden_source"] == "default"
        assert v["browse"] is False and v["config_browse"] is False
        assert v["browser"]["exists"] is False and v["browse_idle_minutes"] == 10
        r = await c.put(
            U, json={"hidden_paths": ["/srv/private/", " ", "/home", "/home"]}, headers=h
        )
        assert r.json()["hidden_paths"] == ["/home", "/srv/private"]
        assert r.json()["hidden_source"] == "app"
        assert last_audit(services)[0] == "settings.host-devices"
        r = await c.put(U, json={"hidden_paths": ["home"]}, headers=h)
        assert r.status_code == 422
        r = await c.put(U, json={"reset_hidden": True}, headers=h)
        assert r.json()["hidden_source"] == "default"
    assert services.browser.calls == []  # the switch didn't change


async def test_switching_the_browser(app, services):
    fake = services.browser = FakeBrowser()
    async with client(app) as c:
        h = await admin(c)
        r = await c.put(U, json={"browse": True}, headers=h)
        assert r.json()["browse"] is True and r.json()["browse_source"] == "app"
        assert r.json()["browser"]["exists"] is True and fake.calls == ["prepare"]
        assert last_audit(services)[:2] == ("host-browser.enable", "mcui-server")
        await c.put(U, json={"browse": True}, headers=h)  # no change: nothing done
        assert fake.calls == ["prepare"]
        r = await c.put(U, json={"browse": False}, headers=h)
        assert r.json()["browser"]["exists"] is False and fake.calls[-1] == "remove"
        assert last_audit(services)[:2] == ("host-browser.remove", "mcui-server")
        await c.put(U, json={"browse": True}, headers=h)
        r = await c.put(U, json={"reset_browse": True}, headers=h)  # the file says off
        assert r.json()["browse_source"] == "default" and fake.calls[-1] == "remove"
        # nothing was there to remove: no audit entry for it
        fake.exists = False
        await c.put(U, json={"browse": True}, headers=h)
        fake.exists = False
        await c.put(U, json={"browse": False}, headers=h)
        assert last_audit(services)[0] == "settings.host-devices"


async def test_switching_on_fails_switching_off_stays_off(app, services):
    fake = services.browser = FakeBrowser()
    async with client(app) as c:
        h = await admin(c)
        fake.fail = BrowseError("project mcui-server exists and isn't the app's", 409)
        r = await c.put(U, json={"browse": True}, headers=h)
        assert r.status_code == 409 and "isn't the app's" in r.text
        assert services.store.get_setting(BROWSE_KEY) is False  # back off
        fake.fail = None
        await c.put(U, json={"browse": True}, headers=h)
        fake.fail = LXDError("boom", 500)
        r = await c.put(U, json={"browse": False}, headers=h)
        assert r.status_code == 502
        assert services.store.get_setting(BROWSE_KEY) is False  # stays off: retried later
        fake.fail = RuntimeError("LXD away")
        r = await c.get(U)
        assert r.json()["browser"] == {"project": "mcui-server", "error": "LXD away"}


async def test_without_lxd(tmp_path):
    from mcui.main import create_app

    store = fast_store()
    store.add_user("admin", "adminpw", "admin")
    settings = Settings(server={"data_dir": str(tmp_path), "secure_cookies": False})
    a = create_app(services=build_services(settings, store=store), static_dir=None)
    async with client(a) as c:
        h = {CSRF_HEADER: await login(c, "admin", "adminpw")}
        r = await c.put(U, json={"browse": True}, headers=h)
        assert r.json()["browser"] is None and r.json()["browse"] is True


@respx.mock
async def test_housekeeping(tmp_path, caplog):
    settings = Settings(server={"data_dir": str(tmp_path)}, metrics={"collect": False})
    svc = build_services(settings, store=fast_store(), clients=lxd_pair())
    fake = svc.browser = FakeBrowser()
    caplog.set_level(logging.INFO)
    await svc.browser_housekeeping()  # off: whatever is left goes
    assert fake.calls == ["remove"]
    fake.exists = True
    await svc.browser_housekeeping()
    assert "removed project mcui-server" in caplog.text
    svc.store.set_setting(BROWSE_KEY, True)
    await svc.browser_housekeeping()
    assert fake.calls[-1] == "stop_idle"
    fake.fail = LXDError("LXD away", 503)
    await svc.browser_housekeeping()
    assert "housekeeping: LXD away" in caplog.text
    await Services(settings, svc.store).browser_housekeeping()  # no LXD: nothing


async def test_housekeeping_loop(tmp_path, monkeypatch):
    settings = Settings(server={"data_dir": str(tmp_path)}, metrics={"collect": False})
    svc = build_services(settings, store=fast_store(), clients=lxd_pair())
    fake = svc.browser = FakeBrowser()
    monkeypatch.setattr("mcui.services.HOUSEKEEPING_SECONDS", 0.01)
    await svc.start()
    await asyncio.sleep(0.05)
    await svc.aclose()
    assert fake.calls.count("remove") >= 2


@pytest.mark.parametrize("key", ["hidden_paths", "browse"])
def test_store_settings_survive(tmp_path, key):
    from mcui.auth.store import Store

    s = Store(tmp_path / "mcui.db")
    s.set_setting(key, [1, "a"])
    s.close()
    assert Store(tmp_path / "mcui.db").get_setting(key) == [1, "a"]
