"""Alerts as they fire, clear and are acknowledged (D91), in the app's own database."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

Severity = Literal["warning", "critical"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY,
    key TEXT NOT NULL,
    rule TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    detail TEXT NOT NULL,
    subject TEXT NOT NULL,
    link TEXT NOT NULL,
    project TEXT,
    fired_at REAL NOT NULL,
    last_seen REAL NOT NULL,
    resolved_at REAL,
    ack_by TEXT,
    ack_at REAL,
    data TEXT
);
CREATE INDEX IF NOT EXISTS alerts_open ON alerts(resolved_at, key);
CREATE INDEX IF NOT EXISTS alerts_fired ON alerts(fired_at);
"""


@dataclass
class Alert:
    """Something wrong now, as a rule found it. ``key`` identifies it across rounds."""

    rule: str
    subject: str  # what it's about: an OSD, a pool on a member, a network
    severity: Severity
    title: str
    detail: str
    link: str = ""  # the page that shows it
    project: str | None = None  # project users see only their projects' alerts
    data: dict[str, Any] = field(default_factory=dict)  # e.g. the value and threshold

    @property
    def key(self) -> str:
        return f"{self.rule}:{self.subject}"


class AlertStore:
    def __init__(self, db: sqlite3.Connection, clock: Callable[[], float]) -> None:
        self.db, self.clock = db, clock
        db.executescript(SCHEMA)

    def open(self) -> dict[str, dict[str, Any]]:
        """Alerts not resolved, by key."""
        rows = self.db.execute("SELECT * FROM alerts WHERE resolved_at IS NULL").fetchall()
        return {r["key"]: _row(r) for r in rows}

    def fire(self, a: Alert) -> int:
        now = self.clock()
        cur = self.db.execute(
            "INSERT INTO alerts (key, rule, severity, title, detail, subject, link, project, "
            "fired_at, last_seen, data) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                a.key,
                a.rule,
                a.severity,
                a.title,
                a.detail,
                a.subject,
                a.link,
                a.project,
                now,
                now,
                json.dumps(a.data),
            ),
        )
        return int(cur.lastrowid or 0)

    def touch(self, alert_id: int, a: Alert) -> None:
        """Still true: the latest severity, text, link and value. Turning critical undoes
        an acknowledgement: that's news (D91 review)."""
        self.db.execute(
            "UPDATE alerts SET title = ?, detail = ?, link = ?, data = ?, last_seen = ?, "
            "ack_by = CASE WHEN severity != 'critical' AND ? = 'critical' "
            "THEN NULL ELSE ack_by END, "
            "ack_at = CASE WHEN severity != 'critical' AND ? = 'critical' "
            "THEN NULL ELSE ack_at END, "
            "severity = ? WHERE id = ?",
            (
                a.title,
                a.detail,
                a.link,
                json.dumps(a.data),
                self.clock(),
                a.severity,
                a.severity,
                a.severity,
                alert_id,
            ),
        )

    def resolve(self, alert_id: int) -> None:
        self.db.execute("UPDATE alerts SET resolved_at = ? WHERE id = ?", (self.clock(), alert_id))

    def acknowledge(self, alert_id: int, username: str) -> bool:
        cur = self.db.execute(
            "UPDATE alerts SET ack_by = ?, ack_at = ? WHERE id = ? AND resolved_at IS NULL",
            (username, self.clock(), alert_id),
        )
        return cur.rowcount == 1

    def get(self, alert_id: int) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
        return _row(row) if row else None

    def list(
        self,
        active: bool,
        limit: int = 200,
        before: int | None = None,
        projects: frozenset[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Newest first; ``projects`` (a project user's) filters before the limit."""
        where = "resolved_at IS NULL" if active else "resolved_at IS NOT NULL"
        args: list[Any] = []
        if projects is not None:
            marks = ",".join("?" * len(projects)) or "NULL"
            where += f" AND project IN ({marks})"
            args += sorted(projects)
        if before is not None:
            where += " AND id < ?"
            args.append(before)
        rows = self.db.execute(
            f"SELECT * FROM alerts WHERE {where} ORDER BY id DESC LIMIT ?",  # noqa: S608 - fixed text
            (*args, limit),
        ).fetchall()
        return [_row(r) for r in rows]

    def rename_project(self, old: str, new: str) -> None:
        """Alerts follow a renamed project, like its grants (D91 review)."""
        self.db.execute("UPDATE alerts SET project = ? WHERE project = ?", (new, old))

    def forget_project(self, project: str) -> None:
        """A deleted project: its open alerts resolve, and its history is no longer any
        project's (only global roles see it), so a later project of that name doesn't
        inherit it."""
        self.db.execute(
            "UPDATE alerts SET resolved_at = COALESCE(resolved_at, ?), project = NULL "
            "WHERE project = ?",
            (self.clock(), project),
        )

    def resolve_all(self) -> int:
        """Alerts switched off (``[alerts] enabled = false``): nothing stays open."""
        cur = self.db.execute(
            "UPDATE alerts SET resolved_at = ? WHERE resolved_at IS NULL", (self.clock(),)
        )
        return cur.rowcount

    def prune(self, days: float) -> int:
        """Forget resolved alerts older than ``days``."""
        cutoff = self.clock() - days * 86400
        cur = self.db.execute(
            "DELETE FROM alerts WHERE resolved_at IS NOT NULL AND resolved_at < ?", (cutoff,)
        )
        return cur.rowcount


def _row(r: sqlite3.Row) -> dict[str, Any]:
    out = dict(r)
    out["data"] = json.loads(out["data"] or "{}")
    return out
