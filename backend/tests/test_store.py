import pytest
from argon2 import PasswordHasher

from mcui.auth.store import AuthError, Store
from tests.conftest import fast_store


def test_users_crud(store):
    u = store.add_user("alice", "pw", "admin")
    assert u.is_admin and u.username == "alice"
    store.add_user("bob", "pw")
    assert [(x.username, x.role) for x in store.list_users()] == [
        ("alice", "admin"),
        ("bob", "viewer"),
    ]
    assert store.count_admins() == 1
    store.set_role("bob", "admin")
    assert store.get_user("bob").is_admin
    store.delete_user("bob")
    assert store.get_user("bob") is None


@pytest.mark.parametrize(
    "call,msg",
    [
        (lambda s: s.add_user("alice", "x"), "already exists"),
        (lambda s: s.add_user("", "x"), "required"),
        (lambda s: s.add_user("z", "x", "root"), "role must be"),
        (lambda s: s.set_role("nobody", "admin"), "no such user"),
        (lambda s: s.set_password("nobody", "x"), "no such user"),
        (lambda s: s.set_password("alice", ""), "required"),
        (lambda s: s.delete_user("nobody"), "no such user"),
    ],
)
def test_user_errors(store, call, msg):
    store.add_user("alice", "pw")
    with pytest.raises(AuthError, match=msg):
        call(store)


def test_authenticate(store):
    store.add_user("alice", "secret", "admin")
    assert store.authenticate("alice", "secret").username == "alice"
    assert store.authenticate("alice", "wrong") is None
    assert store.authenticate("nobody", "secret") is None


def test_authenticate_rehashes_weak_hash():
    weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    s = Store(":memory:", hasher=weak)
    s.add_user("alice", "pw")
    s.hasher = PasswordHasher(time_cost=2, memory_cost=16, parallelism=1)
    old = s.db.execute("SELECT password_hash FROM users").fetchone()[0]
    assert s.authenticate("alice", "pw") is not None
    new = s.db.execute("SELECT password_hash FROM users").fetchone()[0]
    assert new != old and "t=2" in new
    s.close()


def test_sessions_and_expiry():
    now = [1000.0]
    s = fast_store(clock=lambda: now[0])
    user = s.add_user("alice", "pw")
    sess = s.create_session(user, ttl_seconds=60)
    got = s.get_session(sess.token)
    assert got.user == user and got.csrf == sess.csrf and got.expires_at == 1060
    assert s.get_session("bogus") is None
    assert Store.csrf_ok(got, sess.csrf)
    assert not Store.csrf_ok(got, "nope")
    assert not Store.csrf_ok(got, None)
    now[0] = 1060
    assert s.get_session(sess.token) is None
    s.close()


def test_logout_and_password_change_end_sessions(store):
    user = store.add_user("alice", "pw")
    a = store.create_session(user, 60)
    b = store.create_session(user, 60)
    store.delete_session(a.token)
    assert store.get_session(a.token) is None
    store.set_password("alice", "new")
    assert store.get_session(b.token) is None


def test_audit(store):
    store.audit("alice", "auth.login")
    store.audit(
        "bob", "user.create", "carol", ok=False, detail={"role": "viewer"}, operation_id="op1"
    )
    entries = store.audit_entries()
    assert [e["action"] for e in entries] == ["user.create", "auth.login"]
    assert entries[0]["detail"] == {"role": "viewer"} and entries[0]["ok"] is False
    assert entries[0]["operation_id"] == "op1"
    assert entries[1]["detail"] is None
    older = store.audit_entries(limit=10, before_id=entries[0]["id"])
    assert [e["action"] for e in older] == ["auth.login"]


def test_open_creates_file(tmp_path):
    s = Store.open(tmp_path / "nested")
    s.add_user("a", "b", "admin")
    s.close()
    assert (tmp_path / "nested" / "mcui.db").is_file()
