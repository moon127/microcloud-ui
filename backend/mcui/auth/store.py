"""SQLite-backed users, project grants, sessions and audit log (AU-1/2/3, D13, D81)."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast, get_args

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from mcui.files import private_db

# global roles; "project" has none: access only through its project grants (D81)
Role = Literal["admin", "viewer", "project"]
ROLES: tuple[str, ...] = get_args(Role)
ProjectRole = Literal["viewer", "operator", "admin"]
PROJECT_ROLES: tuple[str, ...] = get_args(ProjectRole)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'viewer', 'project')),
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS grants (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('viewer', 'operator', 'admin')),
    PRIMARY KEY (user_id, project)
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    username TEXT,
    action TEXT NOT NULL,
    target TEXT,
    ok INTEGER NOT NULL,
    detail TEXT,
    operation_id TEXT
);
CREATE INDEX IF NOT EXISTS audit_ts ON audit(ts);
"""
SCHEMA_VERSION = 1

# 0 -> 1 (D81): the users.role CHECK gains 'project'. SQLite can't alter a CHECK, so the
# table is rebuilt; sessions reference it, so foreign keys are off meanwhile.
MIGRATE_1 = """
PRAGMA foreign_keys = OFF;
BEGIN;
CREATE TABLE users_new (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'viewer', 'project')),
    created_at REAL NOT NULL
);
INSERT INTO users_new SELECT id, username, password_hash, role, created_at FROM users;
DROP TABLE users;
ALTER TABLE users_new RENAME TO users;
PRAGMA user_version = 1;
COMMIT;
PRAGMA foreign_keys = ON;
"""


class AuthError(Exception):
    pass


@dataclass(frozen=True)
class User:
    id: int
    username: str
    role: Role
    # project -> role; only used when ``role`` is "project"
    grants: dict[str, ProjectRole] = field(default_factory=dict)

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


@dataclass(frozen=True)
class Session:
    token: str
    csrf: str
    user: User
    expires_at: float


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Store:
    def __init__(
        self,
        path: Path | str,
        *,
        hasher: PasswordHasher | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if path != ":memory:":
            private_db(Path(path))
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        if path != ":memory:":
            self.db.execute("PRAGMA journal_mode = WAL")
        self._migrate()
        self.hasher = hasher or PasswordHasher()
        self.clock = clock
        self._dummy_hash = self.hasher.hash(secrets.token_hex(8))

    def _migrate(self) -> None:
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        fresh = not self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'users'"
        ).fetchone()
        if version < 1 and not fresh:
            self.db.executescript(MIGRATE_1)
        self.db.executescript(SCHEMA)
        self.db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @classmethod
    def open(cls, data_dir: Path) -> Store:
        return cls(data_dir / "mcui.db")

    def close(self) -> None:
        self.db.close()

    # --- users -------------------------------------------------------------

    def add_user(self, username: str, password: str, role: str = "viewer") -> User:
        _check_role(role)
        if not username or not password:
            raise AuthError("username and password are required")
        try:
            cur = self.db.execute(
                "INSERT INTO users (username, password_hash, role, created_at) VALUES (?,?,?,?)",
                (username, self.hasher.hash(password), role, self.clock()),
            )
        except sqlite3.IntegrityError as e:
            raise AuthError(f"user {username!r} already exists") from e
        return User(cast(int, cur.lastrowid), username, cast(Role, role))

    def _user(self, row: sqlite3.Row) -> User:
        grants = {
            g["project"]: g["role"]
            for g in self.db.execute(
                "SELECT project, role FROM grants WHERE user_id = ? ORDER BY project", (row["id"],)
            )
        }
        return User(row["id"], row["username"], row["role"], grants)

    def get_user(self, username: str) -> User | None:
        row = self.db.execute(
            "SELECT id, username, role FROM users WHERE username = ?", (username,)
        ).fetchone()
        return self._user(row) if row else None

    def list_users(self) -> list[User]:
        rows = self.db.execute("SELECT id, username, role FROM users ORDER BY username")
        return [self._user(r) for r in rows.fetchall()]

    def set_password(self, username: str, password: str) -> None:
        if not password:
            raise AuthError("password is required")
        cur = self.db.execute(
            "UPDATE users SET password_hash = ? WHERE username = ?",
            (self.hasher.hash(password), username),
        )
        if cur.rowcount == 0:
            raise AuthError(f"no such user {username!r}")
        self.db.execute(
            "DELETE FROM sessions WHERE user_id = (SELECT id FROM users WHERE username = ?)",
            (username,),
        )

    def set_role(self, username: str, role: str) -> None:
        _check_role(role)
        cur = self.db.execute("UPDATE users SET role = ? WHERE username = ?", (role, username))
        if cur.rowcount == 0:
            raise AuthError(f"no such user {username!r}")

    def set_grant(self, username: str, project: str, role: str) -> None:
        """Give ``username`` ``role`` in ``project`` (replacing any it had there)."""
        if role not in PROJECT_ROLES:
            raise AuthError(f"project role must be one of {', '.join(PROJECT_ROLES)}")
        if not project:
            raise AuthError("project is required")
        user = self.get_user(username)
        if user is None:
            raise AuthError(f"no such user {username!r}")
        self.db.execute(
            "INSERT INTO grants (user_id, project, role) VALUES (?,?,?) "
            "ON CONFLICT (user_id, project) DO UPDATE SET role = excluded.role",
            (user.id, project, role),
        )

    def remove_grant(self, username: str, project: str) -> None:
        cur = self.db.execute(
            "DELETE FROM grants WHERE project = ? "
            "AND user_id = (SELECT id FROM users WHERE username = ?)",
            (project, username),
        )
        if cur.rowcount == 0:
            raise AuthError(f"{username!r} has no grant on {project!r}")

    def delete_user(self, username: str) -> None:
        cur = self.db.execute("DELETE FROM users WHERE username = ?", (username,))
        if cur.rowcount == 0:
            raise AuthError(f"no such user {username!r}")

    def count_admins(self) -> int:
        row = self.db.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()
        return int(row[0])

    def authenticate(self, username: str, password: str) -> User | None:
        """Check a password; constant work whether or not the user exists."""
        row = self.db.execute(
            "SELECT id, username, role, password_hash FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        stored = row["password_hash"] if row else self._dummy_hash
        try:
            self.hasher.verify(stored, password)
        except (VerificationError, InvalidHashError):
            return None
        if row is None:  # pragma: no cover - the password matched the random dummy
            return None
        if self.hasher.check_needs_rehash(stored):
            self.db.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (self.hasher.hash(password), row["id"]),
            )
        return self._user(row)

    # --- sessions ----------------------------------------------------------

    def create_session(self, user: User, ttl_seconds: float) -> Session:
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        now = self.clock()
        self.db.execute(
            "INSERT INTO sessions (token_hash, user_id, csrf, created_at, expires_at) "
            "VALUES (?,?,?,?,?)",
            (_hash_token(token), user.id, csrf, now, now + ttl_seconds),
        )
        return Session(token, csrf, user, now + ttl_seconds)

    def get_session(self, token: str) -> Session | None:
        now = self.clock()
        self.db.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        row = self.db.execute(
            "SELECT s.csrf, s.expires_at, u.id, u.username, u.role FROM sessions s "
            "JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
            (_hash_token(token),),
        ).fetchone()
        if row is None:
            return None
        return Session(token, row["csrf"], self._user(row), row["expires_at"])

    def delete_session(self, token: str) -> None:
        self.db.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))

    @staticmethod
    def csrf_ok(session: Session, presented: str | None) -> bool:
        return presented is not None and hmac.compare_digest(session.csrf, presented)

    # --- audit -------------------------------------------------------------

    def audit(
        self,
        username: str | None,
        action: str,
        target: str | None = None,
        *,
        ok: bool = True,
        detail: dict[str, Any] | None = None,
        operation_id: str | None = None,
    ) -> None:
        self.db.execute(
            "INSERT INTO audit (ts, username, action, target, ok, detail, operation_id) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                self.clock(),
                username,
                action,
                target,
                int(ok),
                json.dumps(detail) if detail else None,
                operation_id,
            ),
        )

    def audit_entries(self, limit: int = 100, before_id: int | None = None) -> list[dict[str, Any]]:
        q = "SELECT * FROM audit"
        args: list[Any] = []
        if before_id is not None:
            q += " WHERE id < ?"
            args.append(before_id)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        return [
            {
                "id": r["id"],
                "ts": r["ts"],
                "username": r["username"],
                "action": r["action"],
                "target": r["target"],
                "ok": bool(r["ok"]),
                "detail": json.loads(r["detail"]) if r["detail"] else None,
                "operation_id": r["operation_id"],
            }
            for r in self.db.execute(q, args)
        ]


def _check_role(role: str) -> None:
    if role not in ROLES:
        raise AuthError(f"role must be one of {', '.join(ROLES)}")
