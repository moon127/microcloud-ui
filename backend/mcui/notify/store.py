"""Notification channels and their deliveries (D94), in the app's own database.

A channel belongs to the cluster (``project`` is NULL: cluster admins manage it) or to one
project (its admins manage it, and it gets only that project's alerts). Secrets (tokens,
passwords, webhook URLs) are kept apart from the rest of a channel's settings and are
never returned by the API, only whether each is set.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS notify_channels (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    project TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    min_severity TEXT NOT NULL DEFAULT 'warning',
    config TEXT NOT NULL,
    secrets TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS notify_deliveries (
    id INTEGER PRIMARY KEY,
    channel_id INTEGER NOT NULL,
    created_at REAL NOT NULL,
    events TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_at REAL,
    sent_at REAL,
    error TEXT
);
CREATE INDEX IF NOT EXISTS notify_due ON notify_deliveries(status, next_at);
CREATE INDEX IF NOT EXISTS notify_by_channel ON notify_deliveries(channel_id, id);
CREATE TABLE IF NOT EXISTS notify_sent (
    channel_id INTEGER NOT NULL,
    alert_id INTEGER NOT NULL,
    at REAL NOT NULL,
    PRIMARY KEY (channel_id, alert_id)
);
"""

# retries after a failed send, in seconds: about 30 minutes in all
BACKOFF = (30, 60, 120, 300, 600, 900)


class NotifyStore:
    def __init__(self, db: sqlite3.Connection, clock: Callable[[], float]) -> None:
        self.db, self.clock = db, clock
        db.executescript(SCHEMA)

    # --- channels ---------------------------------------------------------------------

    def channels(
        self, project: str | None = None, *, everyone: bool = False
    ) -> list[dict[str, Any]]:
        """Every channel (``everyone``), the cluster's (no project) or one project's."""
        if everyone:
            rows = self.db.execute("SELECT * FROM notify_channels ORDER BY id").fetchall()
        elif project is None:
            rows = self.db.execute(
                "SELECT * FROM notify_channels WHERE project IS NULL ORDER BY id"
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM notify_channels WHERE project = ? ORDER BY id", (project,)
            ).fetchall()
        return [_channel(r) for r in rows]

    def channel(self, channel_id: int) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT * FROM notify_channels WHERE id = ?", (channel_id,)
        ).fetchone()
        return _channel(row) if row else None

    def add(self, ch: dict[str, Any], username: str) -> int:
        cur = self.db.execute(
            "INSERT INTO notify_channels (name, kind, project, enabled, min_severity, config, "
            "secrets, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                ch["name"],
                ch["kind"],
                ch.get("project"),
                int(ch.get("enabled", True)),
                ch.get("min_severity", "warning"),
                json.dumps(ch.get("config", {})),
                json.dumps(ch.get("secrets", {})),
                username,
                self.clock(),
            ),
        )
        return int(cur.lastrowid or 0)

    def update(self, channel_id: int, ch: dict[str, Any]) -> None:
        self.db.execute(
            "UPDATE notify_channels SET name = ?, enabled = ?, min_severity = ?, config = ?, "
            "secrets = ? WHERE id = ?",
            (
                ch["name"],
                int(ch["enabled"]),
                ch["min_severity"],
                json.dumps(ch["config"]),
                json.dumps(ch["secrets"]),
                channel_id,
            ),
        )

    def delete(self, channel_id: int) -> None:
        for table in ("notify_deliveries", "notify_sent"):
            self.db.execute(f"DELETE FROM {table} WHERE channel_id = ?", (channel_id,))  # noqa: S608 - fixed names
        self.db.execute("DELETE FROM notify_channels WHERE id = ?", (channel_id,))

    def rename_project(self, old: str, new: str) -> None:
        """A project's channels follow it when it's renamed, and so do cluster channels
        limited to it."""
        self.db.execute("UPDATE notify_channels SET project = ? WHERE project = ?", (new, old))
        self._filters(old, new)

    def forget_project(self, project: str) -> None:
        """A deleted project's channels go with it: a later project of that name doesn't
        inherit them (as grants, D88), nor cluster channels' filters."""
        for row in self.channels(project):
            self.delete(row["id"])
        self._filters(project, None)

    def _filters(self, old: str, new: str | None) -> None:
        for ch in self.channels():
            only = ch["config"].get("projects") or []
            if old in only:
                only = [new if p == old else p for p in only if new is not None or p != old]
                # an empty list means every alert: a channel left with none is switched off
                enabled = ch["enabled"] and bool(only)
                config = {**ch["config"], "projects": only}
                self.update(ch["id"], {**ch, "enabled": enabled, "config": config})

    # --- deliveries -------------------------------------------------------------------

    def queue(self, channel_id: int, events: list[dict[str, Any]], *, now: bool = False) -> int:
        """``now``: being sent straight away (a test), so the queue leaves it alone."""
        at = self.clock()
        cur = self.db.execute(
            "INSERT INTO notify_deliveries (channel_id, created_at, events, status, next_at) "
            "VALUES (?,?,?,?,?)",
            (channel_id, at, json.dumps(events), "sending" if now else "pending", at),
        )
        return int(cur.lastrowid or 0)

    def due(self) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT * FROM notify_deliveries WHERE status = 'pending' AND next_at <= ? ORDER BY id",
            (self.clock(),),
        ).fetchall()
        return [_delivery(r) for r in rows]

    def sent(self, delivery_id: int) -> None:
        self.db.execute(
            "UPDATE notify_deliveries SET status = 'sent', attempts = attempts + 1, "
            "sent_at = ?, next_at = NULL, error = NULL WHERE id = ?",
            (self.clock(), delivery_id),
        )

    def failed(self, delivery: dict[str, Any], error: str) -> bool:
        """Record a failed attempt; ``True`` when that was the last one."""
        attempts = delivery["attempts"] + 1
        last: bool = attempts > len(BACKOFF)
        self.db.execute(
            "UPDATE notify_deliveries SET status = ?, attempts = ?, next_at = ?, error = ? "
            "WHERE id = ?",
            (
                "failed" if last else "pending",
                attempts,
                None if last else self.clock() + BACKOFF[attempts - 1],
                error[:500],
                delivery["id"],
            ),
        )
        return last

    def give_up(self, delivery_id: int, error: str, status: str = "failed") -> None:
        """Failed for good, or ``dropped`` (the channel was switched off: not a failure)."""
        self.db.execute(
            "UPDATE notify_deliveries SET status = ?, next_at = NULL, error = ? WHERE id = ?",
            (status, error[:500], delivery_id),
        )

    def deliveries(self, channel_id: int, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT * FROM notify_deliveries WHERE channel_id = ? ORDER BY id DESC LIMIT ?",
            (channel_id, limit),
        ).fetchall()
        return [_delivery(r) for r in rows]

    def failing(self) -> list[dict[str, Any]]:
        """Channels whose latest finished delivery failed for good, with its error."""
        rows = self.db.execute(
            "SELECT d.channel_id, d.error, d.created_at FROM notify_deliveries d "
            "WHERE d.id = (SELECT MAX(id) FROM notify_deliveries x WHERE "
            "x.channel_id = d.channel_id AND x.status IN ('sent', 'failed')) "
            "AND d.status = 'failed'"
        ).fetchall()
        return [dict(r) for r in rows]

    def prune(self, days: float) -> int:
        cutoff = self.clock() - days * 86400
        cur = self.db.execute(
            "DELETE FROM notify_deliveries WHERE created_at < ? AND status IN "
            "('sent', 'failed', 'dropped', 'sending')",
            (cutoff,),
        )
        # what was sent about alerts since cleared outside a round (alerts switched off, a
        # project deleted) needn't be remembered
        self.db.execute(
            "DELETE FROM notify_sent WHERE alert_id NOT IN "
            "(SELECT id FROM alerts WHERE resolved_at IS NULL)"
        )
        return cur.rowcount

    # --- reminders --------------------------------------------------------------------

    def last_sent(self, channel_id: int, alert_id: int) -> float | None:
        row = self.db.execute(
            "SELECT at FROM notify_sent WHERE channel_id = ? AND alert_id = ?",
            (channel_id, alert_id),
        ).fetchone()
        return float(row["at"]) if row else None

    def mark_sent(self, channel_id: int, alert_id: int) -> None:
        self.db.execute(
            "INSERT INTO notify_sent (channel_id, alert_id, at) VALUES (?,?,?) "
            "ON CONFLICT(channel_id, alert_id) DO UPDATE SET at = excluded.at",
            (channel_id, alert_id, self.clock()),
        )

    def forget_alert(self, alert_id: int) -> None:
        self.db.execute("DELETE FROM notify_sent WHERE alert_id = ?", (alert_id,))


def _channel(r: sqlite3.Row) -> dict[str, Any]:
    out = dict(r)
    out["enabled"] = bool(out["enabled"])
    out["config"] = json.loads(out["config"])
    out["secrets"] = json.loads(out["secrets"])
    return out


def _delivery(r: sqlite3.Row) -> dict[str, Any]:
    out = dict(r)
    out["events"] = json.loads(out["events"])
    return out
