"""Sending alerts out (D94): from each alert round's changes to deliveries, sent in the
background and retried for about 30 minutes.

- A channel gets an alert firing, a warning turning critical and, if it was told about the
  alert, its clearing; at or above its minimum severity, without the rules it leaves out,
  and only its project's alerts (a project's channel) or the projects it's limited to.
- One round's events for a channel go out as one message.
- A critical alert nobody has acknowledged is sent again every ``reminder_hours`` (4).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import httpx

from mcui.notify import kinds, oauth
from mcui.notify.net import PinnedTransport, check_public
from mcui.notify.store import NotifyStore

if TYPE_CHECKING:
    from mcui.services import Services

log = logging.getLogger(__name__)
RANK = {"warning": 1, "critical": 2}
DEFAULT_REMINDER_HOURS = 4.0
PARALLEL = 5  # deliveries sent at once
SEND_SECONDS = 20.0  # for a whole message: resolving, connecting, sending, the reply


def wants(ch: dict[str, Any], alert: dict[str, Any]) -> bool:
    """Is this alert one for this channel (whatever its severity)?"""
    if not ch["enabled"]:
        return False
    if ch["project"] is not None and alert.get("project") != ch["project"]:
        return False
    only = [] if ch["project"] is not None else ch["config"].get("projects") or []
    if only and alert.get("project") not in only:
        return False
    return alert.get("rule") not in (ch["config"].get("exclude_rules") or [])


def severe_enough(ch: dict[str, Any], alert: dict[str, Any]) -> bool:
    return RANK.get(str(alert.get("severity")), 0) >= RANK.get(ch["min_severity"], 1)


class Notifier:
    def __init__(
        self,
        services: Services,
        clock: Callable[[], float] = time.time,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.services, self.clock = services, clock
        self.store = NotifyStore(services.store.db, clock)
        self.transport = transport
        # no redirects: a channel sends where it was set to, and only there
        self.http = httpx.AsyncClient(timeout=15, transport=transport, follow_redirects=False)
        self.task: asyncio.Task[int] | None = None
        self.tested: dict[int, float] = {}  # channel: when Send test last ran (monotonic)
        self.sign_ins = oauth.SignIns()
        # httpx logs each request's URL at INFO, and webhook URLs and bot tokens are
        # secrets (D94 review)
        logging.getLogger("httpx").setLevel(logging.WARNING)

    @property
    def app_url(self) -> str | None:
        return self.services.settings.server.public_url

    def handle(self, changes: list[tuple[str, dict[str, Any]]]) -> int:
        """Queue one round's changes; returns the deliveries queued."""
        if not changes:
            return 0
        queued = 0
        for ch in self.store.channels(everyone=True):
            events = []
            for event, alert in changes:
                if not wants(ch, alert):
                    continue
                if event == "resolved":
                    # only what this channel was told about
                    if self.store.last_sent(ch["id"], alert["id"]) is None:
                        continue
                elif not severe_enough(ch, alert):
                    continue
                events.append({"event": event, "alert": alert})
                if event != "resolved":
                    self.store.mark_sent(ch["id"], alert["id"])
            if events:
                self.store.queue(ch["id"], events)
                queued += 1
        for event, alert in changes:
            if event == "resolved":
                self.store.forget_alert(alert["id"])
        return queued

    def reminders(self, open_alerts: list[dict[str, Any]]) -> int:
        """Queue reminders of critical alerts nobody has acknowledged."""
        now = self.clock()
        queued = 0
        crit = [a for a in open_alerts if a["severity"] == "critical" and not a.get("ack_by")]
        if not crit:
            return 0
        for ch in self.store.channels(everyone=True):
            hours = float(ch["config"].get("reminder_hours", DEFAULT_REMINDER_HOURS))
            if hours <= 0:
                continue
            events = []
            for a in crit:
                last = self.store.last_sent(ch["id"], a["id"])
                if last is None or not wants(ch, a) or now - last < hours * 3600:
                    continue
                events.append({"event": "reminder", "alert": a})
                self.store.mark_sent(ch["id"], a["id"])
            if events:
                self.store.queue(ch["id"], events)
                queued += 1
        return queued

    async def deliver(self, ch: dict[str, Any], events: list[dict[str, Any]]) -> None:
        """Send now, within ``SEND_SECONDS``; raises ``NotifyError`` with the reason (no
        secret in it) when it can't. A project's channel connects only to the addresses
        just checked, without a proxy (D94 review)."""
        try:
            await asyncio.wait_for(self._deliver(ch, events), SEND_SECONDS)
        except Exception as e:  # noqa: BLE001 - every failure becomes a reason
            raise kinds.NotifyError(scrub(_reason(e), ch["secrets"])) from e

    async def _deliver(self, ch: dict[str, Any], events: list[dict[str, Any]]) -> None:
        args = (ch["kind"], ch["config"], ch["secrets"], events, self.app_url)
        if ch["project"] is None:
            await kinds.send(self.http, *args)
            return
        pins = await check_public(kinds.targets(ch["kind"], ch["config"], ch["secrets"]))
        inner = self.transport or httpx.AsyncHTTPTransport()
        async with httpx.AsyncClient(
            timeout=15,
            transport=PinnedTransport(pins, inner),
            follow_redirects=False,
            trust_env=False,
        ) as http:
            await kinds.send(http, *args, pins=pins)

    def kick(self) -> None:
        """Send what's due in the background, so a slow server never holds up the alert
        rounds (D94 review); one sending task at a time."""
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.flush())

    async def flush(self) -> int:
        """Send what's due; returns how many went."""
        due = self.store.due()
        if not due:
            return 0
        gate = asyncio.Semaphore(PARALLEL)

        async def one(d: dict[str, Any]) -> bool:
            ch = self.store.channel(d["channel_id"])
            if ch is None:
                return False
            if not ch["enabled"]:
                self.store.give_up(d["id"], "the channel was switched off", status="dropped")
                return False
            async with gate:
                try:
                    await self.deliver(ch, d["events"])
                except Exception as e:  # noqa: BLE001 - recorded for the log, retried
                    if self.store.failed(d, _reason(e)):
                        log.warning("notify: giving up on %s (%s): %s", ch["name"], ch["kind"], e)
                    return False
            self.store.sent(d["id"])
            return True

        return sum(await asyncio.gather(*(one(d) for d in due)))

    async def test(self, ch: dict[str, Any]) -> None:
        """A sample message, sent straight away."""
        alert = {
            "id": 0,
            "key": "test:test",
            "rule": "test",
            "subject": "test",
            "severity": "warning",
            "title": "Test notification from microcloud-ui",
            "detail": f"Channel {ch['name']} works.",
            "link": "/alerts",
            "project": ch["project"],
            "fired_at": self.clock(),
        }
        events = [{"event": "fired", "alert": alert}]
        # in the channel's log, and a success clears a failing-channel alert
        delivery = self.store.queue(ch["id"], events, now=True)
        try:
            await self.deliver(ch, events)
        except kinds.NotifyError as e:
            self.store.give_up(delivery, str(e))
            raise
        self.store.sent(delivery)

    async def aclose(self) -> None:
        if self.task is not None:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        await self.http.aclose()


def scrub(text: str, secrets: dict[str, str]) -> str:
    """The text without any of the channel's secrets in it (an error can quote a header
    or a URL)."""
    for v in secrets.values():
        if v:
            text = text.replace(v, "***")
    return text


def _reason(e: Exception) -> str:
    if isinstance(e, (httpx.TimeoutException, TimeoutError)):
        return "timed out"
    if isinstance(e, httpx.ConnectError):
        return f"can't connect: {e}"[:300]
    if isinstance(e, OSError) and not isinstance(e, TimeoutError):  # email: socket errors
        return f"can't connect: {e.strerror or e}"[:300]
    return str(e) or type(e).__name__
