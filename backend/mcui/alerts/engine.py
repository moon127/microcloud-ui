"""From "what's wrong now" to alerts that fire, update and clear (D91).

Each round every rule returns the alerts it finds, or ``None`` when it couldn't look
(its data is missing or stale): then its open alerts are left as they are, neither
cleared nor refreshed, so an unreachable Ceph mgr doesn't "resolve" a nearfull OSD.

- A new alert fires only once it has been true for its rule's ``for`` period (so one bad
  sample doesn't), counted across rounds while it's pending.
- An open alert is refreshed with the latest severity, text and value.
- An open alert its rule no longer returns is resolved, unless the rule said it couldn't
  see that subject this round (``unknown``: e.g. one member's node_exporter failing).
  Rules apply hysteresis themselves: a threshold rule clears a little below its threshold
  once its alert is open.
- A round where a rule couldn't look restarts its pending alerts' timers, so stale data
  can't make one fire on a single sample.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcui.alerts.store import Alert, AlertStore


class AlertEngine:
    def __init__(self, store: AlertStore, clock: Callable[[], float]) -> None:
        self.store, self.clock = store, clock
        self.pending: dict[str, float] = {}  # key -> when first seen, before firing

    def open_severities(self) -> dict[str, str]:
        """Open alerts' keys and severities, for hysteresis."""
        return {k: r["severity"] for k, r in self.store.open().items()}

    def apply(
        self,
        results: dict[str, list[Alert] | None],
        for_seconds: dict[str, float],
        unknown: frozenset[str] | set[str] = frozenset(),
    ) -> list[dict[str, Any]]:
        """Record one round. ``unknown``: key prefixes the rules couldn't see this round.
        Returns the alerts that fired in it."""
        now = self.clock()
        current = self.store.open()
        fired = []
        for rule, alerts in results.items():
            if alerts is None:  # couldn't look: leave its alerts, restart its timers
                for key in [k for k in self.pending if k.startswith(f"{rule}:")]:
                    self.pending.pop(key)
                continue
            seen = set()
            for a in alerts:
                seen.add(a.key)
                if a.key in current:
                    self.store.touch(current[a.key]["id"], a)
                    continue
                first = self.pending.setdefault(a.key, now)
                if now - first >= for_seconds.get(rule, 0):
                    self.pending.pop(a.key)
                    alert_id = self.store.fire(a)
                    fired.append(self.store.get(alert_id))
            for key, row in current.items():
                # an exact key, or a prefix ending in ":" or "/" (so m1 doesn't hide m10)
                hidden = any(
                    key == u or (u.endswith((":", "/")) and key.startswith(u)) for u in unknown
                )
                if row["rule"] == rule and key not in seen and not hidden:
                    self.store.resolve(row["id"])
            for key in [k for k in self.pending if k.startswith(f"{rule}:") and k not in seen]:
                self.pending.pop(key)
        return [f for f in fired if f]
