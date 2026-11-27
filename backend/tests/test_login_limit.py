"""Sign-in throttling and off-loop hashing (D89, reworked after the D90 review)."""

import asyncio

from mcui.auth.limit import MAX_KEYS, LoginLimiter, address_key
from tests.test_api import client
from tests.test_api_instances import app, services

__all__ = ["app", "services"]  # fixtures


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def test_limits():
    clock = Clock()
    lim = LoginLimiter(per_user_address=3, per_address=5, per_user=4, window=60, clock=clock)
    for _ in range(3):
        assert lim.attempt("192.0.2.1", "Pat") == 0
        clock.t += 1
    assert lim.attempt("192.0.2.1", "pat") == 57  # this user from this address
    # the owner, from elsewhere, isn't locked out by one attacker
    assert lim.attempt("192.0.2.9", "pat") == 0
    # but a distributed guess hits the per-user ceiling (4)
    assert lim.attempt("192.0.2.8", "pat") > 0
    assert lim.attempt("192.0.2.1", "a") == 0
    assert lim.attempt("192.0.2.1", "b") == 0  # five counted from this address now
    assert lim.attempt("192.0.2.1", "c") > 0  # refused ones don't count, so the sixth
    clock.t = 70
    assert lim.attempt("192.0.2.1", "pat") == 0  # the window moved on


def test_success_gives_the_slot_back():
    lim = LoginLimiter(per_user_address=2, per_address=10, per_user=10, clock=Clock())
    for _ in range(5):
        assert lim.attempt("192.0.2.1", "pat") == 0
        lim.succeeded("192.0.2.1", "pat")


def test_ipv6_by_64_and_eviction_spares_locked_counters():
    assert address_key("2001:db8::1") == address_key("2001:db8::ffff") == "2001:db8::/64"
    assert address_key("192.0.2.1") == "192.0.2.1" and address_key("x") == "x"
    lim = LoginLimiter(per_user_address=1, per_address=10**9, per_user=10**9, clock=Clock())
    lim.attempt("192.0.2.1", "admin")  # admin locked from this address
    for i in range(MAX_KEYS):
        lim.attempt(f"198.51.100.{i % 250}", f"u{i}")
    assert lim.attempt("192.0.2.1", "admin") > 0  # still counted
    assert len(lim._seen) <= MAX_KEYS + 3


async def test_parallel_attempts_dont_slip_through(app, services, monkeypatch):
    """A burst of wrong passwords at once: only the limit's worth reach the hasher."""
    real = services.store.verify
    calls = []

    def slow(stored, password):
        calls.append(1)
        return real(stored, password)

    monkeypatch.setattr(services.store, "verify", slow)
    async with client(app) as c:
        rs = await asyncio.gather(
            *(
                c.post("/api/auth/login", json={"username": "admin", "password": "x"})
                for _ in range(12)
            )
        )
    assert sorted(r.status_code for r in rs) == [401] * 5 + [429] * 7
    assert len(calls) == 5


async def test_login_is_throttled_and_audited_carefully(app, services):
    async with client(app) as c:
        for _ in range(5):
            r = await c.post("/api/auth/login", json={"username": "admin", "password": "x"})
            assert r.status_code == 401
        r = await c.post("/api/auth/login", json={"username": "admin", "password": "adminpw"})
        assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
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
