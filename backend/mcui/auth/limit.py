"""Throttling sign-in attempts (D89, reworked after the D90 review).

Each attempt counts *before* its password is checked, so a burst of parallel attempts
can't all slip past the limit while they wait for the hasher; a success gives its slot
back. Three limits in a window (5 minutes):

- 5 per username from one address: one client can't keep guessing an account, and can't
  lock its owner out from elsewhere;
- 30 per address (IPv6 by /64): one client can't spray many accounts;
- 50 per username from anywhere: a distributed guess is still capped.

A refused attempt waits until its oldest counted attempt leaves the window (429 with
Retry-After). In memory: a restart forgets it, which is fine for a throttle. The address
is the one uvicorn reports: the client's when ``forwarded_allow_ips`` names the proxy,
the proxy's otherwise (then everyone shares one per-address count; docs/DEPLOY.md).
"""

from __future__ import annotations

import ipaddress
import time
from collections import OrderedDict, deque
from collections.abc import Callable

MAX_KEYS = 10_000  # counters kept; ones at their limit are evicted last


def address_key(address: str) -> str:
    """IPv6 addresses by /64 (one host can have billions); others as they are."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return address
    if ip.version == 6:
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


class LoginLimiter:
    def __init__(
        self,
        per_user_address: int = 5,
        per_address: int = 30,
        per_user: int = 50,
        window: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limits = {"ua": per_user_address, "a": per_address, "u": per_user}
        self.window, self.clock = window, clock
        self._seen: OrderedDict[str, deque[float]] = OrderedDict()

    def _keys(self, address: str, username: str) -> dict[str, str]:
        user, addr = username.lower(), address_key(address)
        return {"ua": f"ua:{user}|{addr}", "a": f"a:{addr}", "u": f"u:{user}"}

    def _recent(self, key: str) -> deque[float]:
        q = self._seen.get(key)
        if q is None:
            return deque()
        now = self.clock()
        while q and now - q[0] >= self.window:
            q.popleft()
        if not q:
            del self._seen[key]
        return q

    def attempt(self, address: str, username: str) -> float:
        """Count an attempt, unless one of its limits is reached: then the seconds to
        wait (and nothing counted). 0 means go ahead and check the password."""
        keys = self._keys(address, username)
        wait = 0.0
        for kind, key in keys.items():
            q = self._recent(key)
            if len(q) >= self.limits[kind]:
                wait = max(wait, q[0] + self.window - self.clock())
        if wait > 0:
            return wait
        now = self.clock()
        for key in keys.values():
            self._seen.setdefault(key, deque()).append(now)
            self._seen.move_to_end(key)
        self._evict()
        return 0.0

    def succeeded(self, address: str, username: str) -> None:
        """A correct password: its attempt doesn't count, and this user's slate from this
        address is clean."""
        keys = self._keys(address, username)
        self._seen.pop(keys["ua"], None)
        for key in (keys["a"], keys["u"]):
            q = self._seen.get(key)
            if q:
                q.pop()

    def _evict(self) -> None:
        """Drop the oldest counters past ``MAX_KEYS``, but not ones at their limit: else
        many addresses could flush a locked-out username's count."""
        if len(self._seen) <= MAX_KEYS:
            return
        for key in list(self._seen):
            if len(self._seen) <= MAX_KEYS:
                return
            if len(self._recent(key)) < self.limits[key.split(":", 1)[0]]:
                self._seen.pop(key, None)
