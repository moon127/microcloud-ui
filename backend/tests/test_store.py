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


def test_project_grants(store):
    """A "project" user has no global role; access comes only from its grants (D81)."""
    store.add_user("pat", "pw", "project")
    store.set_grant("pat", "web", "operator")
    store.set_grant("pat", "lab", "viewer")
    store.set_grant("pat", "web", "admin")  # replaces
    u = store.get_user("pat")
    assert (u.role, u.is_admin, u.grants) == ("project", False, {"lab": "viewer", "web": "admin"})
    assert store.authenticate("pat", "pw").grants == u.grants
    session = store.create_session(u, 60)
    assert store.get_session(session.token).user.grants == u.grants
    assert [x.grants for x in store.list_users() if x.username == "pat"] == [u.grants]
    store.remove_grant("pat", "lab")
    assert store.get_user("pat").grants == {"web": "admin"}
    store.delete_user("pat")  # grants go with the user
    assert store.db.execute("SELECT COUNT(*) FROM grants").fetchone()[0] == 0


@pytest.mark.parametrize(
    "call,msg",
    [
        (lambda s: s.set_grant("pat", "web", "owner"), "project role must be"),
        (lambda s: s.set_grant("pat", "", "viewer"), "project is required"),
        (lambda s: s.set_grant("nobody", "web", "viewer"), "no such user"),
        (lambda s: s.remove_grant("pat", "web"), "has no grant"),
    ],
)
def test_grant_errors(store, call, msg):
    store.add_user("pat", "pw", "project")
    with pytest.raises(AuthError, match=msg):
        call(store)


def test_migrates_a_database_from_before_project_users(tmp_path):
    """Before D81 users.role allowed only admin/viewer; the table is rebuilt, keeping users
    and their sessions."""
    import sqlite3

    path = tmp_path / "mcui.db"
    old = sqlite3.connect(path)
    old.executescript(
        """
        CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('admin', 'viewer')), created_at REAL NOT NULL);
        CREATE TABLE sessions (token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            csrf TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL);
        INSERT INTO users VALUES (1, 'alice', 'x', 'admin', 0);
        INSERT INTO sessions VALUES ('h', 1, 'c', 0, 9e12);
        """
    )
    old.close()
    store = Store(path, hasher=PasswordHasher(time_cost=1, memory_cost=8, parallelism=1))
    assert store.db.execute("PRAGMA user_version").fetchone()[0] == 1
    assert store.get_user("alice").role == "admin"
    assert store.db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
    store.add_user("pat", "pw", "project")  # the new role is accepted
    assert store.db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    store.close()
    again = Store(path)  # opening again doesn't migrate twice
    assert [u.username for u in again.list_users()] == ["alice", "pat"]
