"""Running the alert rules every ``[alerts] interval_seconds`` (D91)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from mcui.alerts.engine import AlertEngine
from mcui.alerts.rules import RULES, Context, evaluate
from mcui.alerts.store import AlertStore
from mcui.config import DEFAULT_THRESHOLDS

if TYPE_CHECKING:
    from mcui.services import Services

log = logging.getLogger(__name__)
THRESHOLDS_KEY = "alerts.thresholds"
DISABLED_KEY = "alerts.disabled"
PRUNE_EVERY = 3600.0


class AlertService:
    def __init__(self, services: Services, clock: Callable[[], float] = time.time) -> None:
        self.services, self.clock = services, clock
        self.store = AlertStore(services.store.db, clock)
        self.engine = AlertEngine(self.store, clock)
        self._pruned = 0.0

    def thresholds(self) -> dict[str, tuple[float, float]]:
        """Saved in the app, else the config file, else the defaults."""
        saved = self.services.store.get_setting(THRESHOLDS_KEY) or {}
        cfg = self.services.settings.alerts.thresholds
        out: dict[str, tuple[float, float]] = {}
        for k, v in DEFAULT_THRESHOLDS.items():
            warn, crit = saved.get(k) or cfg.get(k) or v
            out[k] = (float(warn), float(crit))
        return out

    def disabled(self) -> set[str]:
        saved = self.services.store.get_setting(DISABLED_KEY)
        return set(saved if saved is not None else self.services.settings.alerts.disabled)

    async def run_once(self) -> list[dict[str, Any]]:
        """One round: evaluate every rule, record what changed. Returns the alerts fired."""
        ctx = Context(self.services, self.engine.open_severities(), self.thresholds(), self.clock())
        results = await evaluate(ctx, self.disabled())
        fired = self.engine.apply(results, {n: f for n, (_, f) in RULES.items()}, ctx.unknown)
        if self.clock() - self._pruned > PRUNE_EVERY:
            self.store.prune(self.services.settings.alerts.retention_days)
            self._pruned = self.clock()
        for a in fired:
            log.warning("alert: %s %s: %s", a["severity"], a["key"], a["title"])
        return fired
