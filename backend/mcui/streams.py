"""Console/terminal sessions started by the backend; see mcui.api.streams."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from websockets.asyncio.client import ClientConnection

STREAM_TTL = 3600.0


@dataclass
class Stream:
    id: str
    kind: str
    owner: str
    project: str
    instance: str
    operation: str
    base: str | None
    secrets: dict[str, str]
    created: float = field(default_factory=time.monotonic)
    # LXD waits until *every* websocket of an exec/console operation is connected,
    # so the backend holds the control channel while any browser is attached.
    control: ClientConnection | None = None
    attached: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def acquire_control(
        self, connect: Callable[[str], Awaitable[ClientConnection]]
    ) -> ClientConnection | None:
        async with self.lock:
            self.attached += 1
            if self.control is None and "control" in self.secrets:
                self.control = await connect(self.secrets["control"])
            return self.control

    async def release_control(self) -> None:
        async with self.lock:
            self.attached -= 1
            if self.attached <= 0 and self.control is not None:
                with contextlib.suppress(Exception):
                    await self.control.close()
                self.control = None


class StreamRegistry:
    def __init__(self) -> None:
        self._streams: dict[str, Stream] = {}

    def add(self, s: Stream) -> None:
        now = time.monotonic()
        self._streams = {k: v for k, v in self._streams.items() if now - v.created < STREAM_TTL}
        self._streams[s.id] = s

    def get(self, stream_id: str) -> Stream | None:
        s = self._streams.get(stream_id)
        if s is not None and time.monotonic() - s.created >= STREAM_TTL:
            del self._streams[stream_id]
            return None
        return s
