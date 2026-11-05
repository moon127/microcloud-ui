"""LXD background operations: start, wait, cancel, list (CL-8)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mcui.lxd.client import LXDClient, LXDError

MAX_WAIT = 30  # seconds per wait call; clients poll again for longer operations


@dataclass
class Started:
    """Result of a mutating call: the operation (if async) and any sync metadata."""

    operation: dict[str, Any] | None
    metadata: Any = None

    @property
    def operation_id(self) -> str | None:
        return str(self.operation["id"]) if self.operation else None

    def as_dict(self) -> dict[str, Any]:
        return {"operation": self.operation, "metadata": self.metadata}


async def start(
    client: LXDClient,
    method: str,
    path: str,
    *,
    json: Any = None,
    params: dict[str, Any] | None = None,
) -> Started:
    """Send a mutating request. Async LXD responses carry the operation as metadata."""
    r = await client.raw(method, path, json=json, params=params)
    try:
        body = r.json()
    except ValueError as e:
        raise LXDError(f"non-JSON response (HTTP {r.status_code})", r.status_code) from e
    if not isinstance(body, dict) or body.get("type") == "error" or r.status_code >= 400:
        msg = body.get("error") if isinstance(body, dict) else None
        code = body.get("error_code") if isinstance(body, dict) else None
        raise LXDError(str(msg or f"HTTP {r.status_code}"), int(code or r.status_code))
    if body.get("type") == "async":
        return Started(operation=body.get("metadata"))
    return Started(operation=None, metadata=body.get("metadata"))


async def wait(client: LXDClient, op_id: str, timeout: int = MAX_WAIT) -> dict[str, Any]:
    """Wait up to ``timeout`` seconds; returns the operation (maybe still running)."""
    timeout = max(0, min(timeout, MAX_WAIT))
    op: dict[str, Any] = await client.get(f"/1.0/operations/{op_id}/wait", timeout=timeout)
    return op


async def get(client: LXDClient, op_id: str) -> dict[str, Any]:
    op: dict[str, Any] = await client.get(f"/1.0/operations/{op_id}")
    return op


async def cancel(client: LXDClient, op_id: str) -> None:
    await client.query("DELETE", f"/1.0/operations/{op_id}")


async def run(
    client: LXDClient,
    method: str,
    path: str,
    *,
    json: Any = None,
    params: dict[str, Any] | None = None,
    timeout: int = MAX_WAIT,
) -> Started:
    """Start and wait for completion; raise if the operation failed."""
    st = await start(client, method, path, json=json, params=params)
    if st.operation is not None:
        op = await wait(client, str(st.operation["id"]), timeout)
        st.operation = op
        if op.get("status") == "Failure":
            raise LXDError(op.get("err") or "operation failed", 500)
    return st


async def list_all(client: LXDClient) -> list[dict[str, Any]]:
    """Every operation in every project, newest first, flattened with its status."""
    data = await client.get("/1.0/operations", recursion=1, **{"all-projects": "true"}) or {}
    ops = [op for group in data.values() for op in (group or [])]
    return sorted(ops, key=lambda op: str(op.get("created_at", "")), reverse=True)
