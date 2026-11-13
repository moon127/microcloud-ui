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
    headers: dict[str, str] | None = None,
    content: Any = None,
    timeout: float | None = None,
) -> Started:
    """Send a mutating request. Async LXD responses carry the operation as metadata.

    ``content`` (with ``headers``) sends a raw body instead, such as a streamed upload.
    """
    r = await client.raw(
        method, path, json=json, params=params, headers=headers, content=content, timeout=timeout
    )
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


FINAL = frozenset({"Success", "Failure", "Cancelled"})


async def wait(client: LXDClient, op_id: str, timeout: int = MAX_WAIT) -> dict[str, Any]:
    """Wait up to ``timeout`` seconds; returns the operation (maybe still running)."""
    timeout = max(0, min(timeout, MAX_WAIT))
    # LXD holds the request for up to ``timeout`` s, so the HTTP timeout must be longer
    try:
        op: dict[str, Any] = await client.query(
            "GET",
            f"/1.0/operations/{op_id}/wait",
            params={"timeout": timeout},
            timeout=timeout + 15,
        )
    except LXDError as e:
        # an expired wait is reported as an error (seen live: "context deadline
        # exceeded"); the operation is simply still running
        if e.status == 504 or "deadline exceeded" in str(e):
            return await get(client, op_id)
        # a failed operation's wait also answers with its error (seen live on a
        # volume snapshot); hand back the operation itself, with status and err
        try:
            failed = await get(client, op_id)
        except LXDError:
            raise e from None
        if failed.get("status") in FINAL:
            return failed
        raise
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
    """Every operation in every project, newest first, flattened with its status.

    ``recursion=2`` nests a bulk operation's children under it (LXD 6.8+, ``children``
    and ``child_count``); LXD also lists them at the top level, so those are dropped
    there. Older LXD treats it like ``recursion=1``."""
    data = await client.get("/1.0/operations", recursion=2, **{"all-projects": "true"}) or {}
    ops = [op for group in data.values() for op in (group or [])]
    nested = {str(c.get("id")) for op in ops for c in op.get("children") or []}
    ops = [op for op in ops if str(op.get("id")) not in nested]
    return sorted(ops, key=lambda op: str(op.get("created_at", "")), reverse=True)
