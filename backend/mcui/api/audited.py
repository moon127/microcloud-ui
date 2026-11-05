"""Run an LXD change on behalf of a user and record it in the audit log (AU-3)."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any

from mcui.auth.store import Session, Store
from mcui.lxd.client import LXDError
from mcui.lxd.operations import Started


async def audited(
    session: Session,
    store: Store,
    action: str,
    target: str,
    call: Awaitable[Started],
    detail: dict[str, Any] | None = None,
) -> Started:
    try:
        st = await call
    except LXDError as e:
        store.audit(
            session.user.username,
            action,
            target,
            ok=False,
            detail={**(detail or {}), "error": str(e)},
        )
        raise
    store.audit(session.user.username, action, target, detail=detail, operation_id=st.operation_id)
    return st
