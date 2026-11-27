"""The uvicorn server ``mcui serve`` runs, with one change for containers.

After a graceful shutdown on SIGTERM, uvicorn sends itself the signal again so the
process ends "killed by SIGTERM". As PID 1 in a container (the image runs ``mcui`` with
no init), that fails with ``PermissionError``: the process exits 1 and systemd marks the
Quadlet unit failed on every stop (seen live with Podman 4.9). PID 1 has no default
action to re-raise into anyway, so there it exits 0 after shutting down. Podman's
``--init`` isn't the answer: under its catatonit the server never saw SIGTERM and was
killed after 10 s (also seen live).
"""

from __future__ import annotations

import os
from types import FrameType

import uvicorn

STARTUP_FAILURE = 3  # as uvicorn.run exits when the server never started


class Server(uvicorn.Server):
    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        super().handle_exit(sig, frame)
        # a private uvicorn attribute (0.29+): if a release renames it, stopping still
        # works, only as before this fix (D90)
        captured = getattr(self, "_captured_signals", None)
        if os.getpid() == 1 and isinstance(captured, list):
            captured.clear()


def serve(app: str, **kwargs: object) -> int:
    """Run ``app`` until stopped; the exit status, as ``uvicorn.run`` would give it."""
    server = Server(uvicorn.Config(app, **kwargs))  # type: ignore[arg-type]
    server.run()
    return 0 if server.started else STARTUP_FAILURE
