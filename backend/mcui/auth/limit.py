"""Throttling failed sign-ins (0.3.4 follow-up, D88).

Before, any client could try passwords as fast as the server would hash them. Now a
username, or a client address, that fails too often in a window has to wait: the server
answers 429 with Retry-After until the oldest failure leaves the window. A success clears
that username's failures. In memory: a restart forgets them, which is fine for a
throttle. The address is the one uvicorn reports, the proxy's client when
``forwarded_allow_ips`` names the proxy (D65).
"""

from __future__ import annotations

import time
from collections import OrderedDict, deque
from collections.abc import Callable

MAX_KEYS = 10_000  # failures are kept for this many usernames and addresses at most


class LoginLimiter:
    def __init__(
        self,
        per_user: int = 5,
        per_address: int = 30,
        window: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.per_user, self.per_address, self.window, self.clock = (
            per_user,
            per_address,
            window,
            clock,
        )
        self._fails: OrderedDict[str, deque[float]] = OrderedDict()

    def _recent(self, key: str) -> deque[float]:
        now = self.clock()
        q = self._fails.get(key)
        if q is None:
            return deque()
        while q and now - q[0] >= self.window:
            q.popleft()
        if not q:
            del self._fails[key]
        return q

    def _wait(self, key: str, limit: int) -> float:
        q = self._recent(key)
        if len(q) < limit:
            return 0.0
        return max(0.0, q[0] + self.window - self.clock())

    def retry_after(self, address: str, username: str) -> float:
        """Seconds to wait before trying again; 0 when allowed."""
        return max(
            self._wait(f"u:{username.lower()}", self.per_user),
            self._wait(f"a:{address}", self.per_address),
        )

    def failed(self, address: str, username: str) -> None:
        now = self.clock()
        for key in (f"u:{username.lower()}", f"a:{address}"):
            q = self._fails.setdefault(key, deque())
            q.append(now)
            self._fails.move_to_end(key)
        while len(self._fails) > MAX_KEYS:
            self._fails.popitem(last=False)

    def succeeded(self, username: str) -> None:
        self._fails.pop(f"u:{username.lower()}", None)
