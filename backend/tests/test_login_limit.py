"""Failed sign-ins are throttled and hashing is off the event loop (0.3.4 follow-up, D88)."""

import asyncio

from mcui.auth.limit import MAX_KEYS, LoginLimiter
from tests.test_api import client
from tests.test_api_instances import app, services

__all__ = ["app", "services"]  # fixtures


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def test_limiter():
    clock = Clock()
    lim = LoginLimiter(per_user=3, per_address=5, window=60, clock=clock)
    for _ in range(3):
        assert lim.retry_after("192.0.2.1", "Pat") == 0
        lim.failed("192.0.2.1", "Pat")
        clock.t += 1
    assert lim.retry_after("192.0.2.9", "pat") == 57  # the username, from anywhere
    assert lim.retry_after("192.0.2.1", "alice") == 0  # the address isn't at its limit yet
    lim.failed("192.0.2.1", "a")
    lim.failed("192.0.2.1", "b")
    assert lim.retry_after("192.0.2.1", "carol") > 0  # five from one address
    clock.t = 60.5  # the first failures leave the window
    assert lim.retry_after("192.0.2.9", "pat") == 0
    lim.failed("192.0.2.2", "pat")
    lim.succeeded("PAT")
    assert lim.retry_after("192.0.2.2", "pat") == 0
    clock.t = 1000
    assert lim.retry_after("192.0.2.1", "x") == 0 and "a:192.0.2.1" not in lim._fails


def test_limiter_keeps_bounded():
    lim = LoginLimiter()
    for i in range(MAX_KEYS):
        lim.failed(f"a{i}", "u")
    assert len(lim._fails) == MAX_KEYS


async def test_login_is_throttled_and_audited_carefully(app, services):
    async with client(app) as c:
        for _ in range(5):
            r = await c.post("/api/auth/login", json={"username": "admin", "password": "x"})
            assert r.status_code == 401
        r = await c.post("/api/auth/login", json={"username": "admin", "password": "adminpw"})
        assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
        # someone typing a password into the username box: not recorded as typed
        services.logins = type(services.logins)()
        await c.post("/api/auth/login", json={"username": "hunter2-secret", "password": "x"})
        e = services.store.audit_entries(limit=1)[0]
        assert e["action"] == "auth.login" and not e["ok"]
        assert e["username"] is None and e["detail"] == {"username": "(unknown)"}
        r = await c.post("/api/auth/login", json={"username": "admin", "password": "adminpw"})
        assert r.status_code == 200
        assert services.store.audit_entries(limit=1)[0]["username"] == "admin"


async def test_hashing_runs_in_a_thread(app, monkeypatch):
    calls = []
    real = asyncio.to_thread

    async def spy(fn, *a, **kw):
        calls.append(fn.__name__)
        return await real(fn, *a, **kw)

    monkeypatch.setattr("mcui.api.auth.asyncio.to_thread", spy)
    async with client(app) as c:
        r = await c.post("/api/auth/login", json={"username": "admin", "password": "adminpw"})
    assert r.status_code == 200 and calls == ["verify"]
