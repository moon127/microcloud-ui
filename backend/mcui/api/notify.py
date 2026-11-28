"""Notification channels (D94): where alerts are sent.

Cluster channels (``/api/notify``) are a cluster admin's and can get every alert. A
project's channels (``/api/projects/{project}/notify``) are its admins' and get only that
project's alerts; they may only send to public addresses (``mcui.notify.net``). Secrets are
write-only: the API says which are set, never what they are.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

import httpx
from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, Field

from mcui.alerts.rules import RULES
from mcui.api.deps import AdminDep, ProjectManageDep, ScopeDep, ServicesDep, StoreDep
from mcui.api.instances import NAME, ProjectName
from mcui.auth.store import Session, Store
from mcui.notify import kinds
from mcui.notify.kinds import KINDS
from mcui.notify.net import NotPublic, check_public
from mcui.notify.service import DEFAULT_REMINDER_HOURS, Notifier
from mcui.services import Services

router = APIRouter(prefix="/api")
ChannelId = Annotated[int, Path(ge=1)]
Value = Annotated[str, Field(max_length=2000)]
# a channel's name shows in alerts sent to other channels: plain words only (D94 review)
ChannelName = Annotated[str, Field(pattern=r"^\w[\w .,()+'-]{0,63}$")]
Project = Annotated[str, Field(pattern=NAME, max_length=63)]


class ChannelIn(BaseModel):
    name: ChannelName
    kind: Literal["slack", "discord", "teams", "webhook", "telegram", "ntfy"]
    enabled: bool = True
    min_severity: Literal["warning", "critical"] = "warning"
    config: dict[str, Value] = Field(default_factory=dict, max_length=10)
    # absent: keep what's saved; "": remove it
    secrets: dict[str, Value] = Field(default_factory=dict, max_length=10)
    reminder_hours: float = Field(default=DEFAULT_REMINDER_HOURS, ge=0, le=168)
    exclude_rules: list[str] = Field(default_factory=list, max_length=50)
    # a cluster channel limited to these projects' alerts; empty: every alert
    projects: list[Project] = Field(default_factory=list, max_length=200)


class TelegramChats(BaseModel):
    token: Value | None = None  # a new bot's, before the channel is saved
    channel_id: int | None = None  # or a saved channel's


def _notifier(services: Services) -> Notifier:
    if services.notifier is None:  # pragma: no cover - always built
        raise HTTPException(503, "notifications aren't running")
    return services.notifier


def view(n: Notifier, ch: dict[str, Any]) -> dict[str, Any]:
    last = n.store.deliveries(ch["id"], 1)
    return {
        "id": ch["id"],
        "name": ch["name"],
        "kind": ch["kind"],
        "project": ch["project"],
        "enabled": ch["enabled"],
        "min_severity": ch["min_severity"],
        "config": {k: v for k, v in ch["config"].items() if k in KINDS[ch["kind"]].config},
        "secrets_set": sorted(k for k, v in ch["secrets"].items() if v),
        "reminder_hours": ch["config"].get("reminder_hours", DEFAULT_REMINDER_HOURS),
        "exclude_rules": ch["config"].get("exclude_rules", []),
        "projects": ch["config"].get("projects", []),
        "created_by": ch["created_by"],
        "created_at": ch["created_at"],
        "last": (
            {k: last[0][k] for k in ("status", "error", "created_at", "sent_at")} if last else None
        ),
    }


async def _clean(
    body: ChannelIn, project: str | None, saved: dict[str, Any] | None
) -> dict[str, Any]:
    """The channel to store, or 400 with why not."""
    kind = KINDS[body.kind]
    unknown = set(body.config) - set(kind.config)
    unknown |= set(body.secrets) - set(kind.secrets)
    if unknown:
        raise HTTPException(400, f"{body.kind} has no setting {', '.join(sorted(unknown))}")
    bad = set(body.exclude_rules) - set(RULES)
    if bad:
        raise HTTPException(400, f"no such rule: {', '.join(sorted(bad))}")
    if project is not None and body.projects:
        raise HTTPException(400, "a project's channel gets only its project's alerts")
    config = {**kind.defaults, **{k: v.strip() for k, v in body.config.items()}}
    secrets = dict(saved["secrets"]) if saved else {}
    for k, v in body.secrets.items():
        if v:
            secrets[k] = v.strip()
        else:
            secrets.pop(k, None)
    if saved:
        # a saved token or password goes only where it was meant for: pointing the channel
        # at another server needs it typed again (D94 review)
        before = kinds.targets(body.kind, saved["config"], saved["secrets"])
        if kinds.targets(body.kind, config, secrets) != before:
            kept = sorted(k for k in saved["secrets"] if k in secrets and k not in body.secrets)
            kept = [k for k in kept if k not in kinds.URL_FIELDS]
            if kept:
                raise HTTPException(
                    400, f"the server changed: enter {', '.join(kept)} again, or remove it"
                )
    try:
        kinds.check(body.kind, config, secrets, cluster=project is None)
        if project is not None:
            await check_public(kinds.targets(body.kind, config, secrets))
    except NotPublic as e:
        raise HTTPException(400, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {
        "name": body.name.strip(),
        "kind": body.kind,
        "project": project,
        "enabled": body.enabled,
        "min_severity": body.min_severity,
        "config": {
            **config,
            "reminder_hours": body.reminder_hours,
            "exclude_rules": body.exclude_rules,
            "projects": body.projects,
        },
        "secrets": secrets,
    }


def _target(ch: dict[str, Any]) -> str:
    return f"{ch['project'] or 'cluster'}/{ch['name']}"


def _get(n: Notifier, channel_id: int, project: str | None) -> dict[str, Any]:
    ch = n.store.channel(channel_id)
    # a project's routes see only its channels; the cluster routes see every channel
    if ch is None or (project is not None and ch["project"] != project):
        raise HTTPException(404, "no such channel")
    return ch


# --- shared handlers --------------------------------------------------------------------


def _list(services: Services, project: str | None) -> list[dict[str, Any]]:
    n = _notifier(services)
    rows = n.store.channels(project, everyone=project is None)
    return [view(n, ch) for ch in rows]


async def _create(
    services: Services, store: Store, session: Session, project: str | None, body: ChannelIn
) -> dict[str, Any]:
    n = _notifier(services)
    ch = await _clean(body, project, None)
    channel_id = n.store.add(ch, session.user.username)
    store.audit(session.user.username, "notify.create", _target(ch), detail={"kind": ch["kind"]})
    return view(n, _get(n, channel_id, project))


async def _update(
    services: Services,
    store: Store,
    session: Session,
    project: str | None,
    channel_id: int,
    body: ChannelIn,
) -> dict[str, Any]:
    n = _notifier(services)
    saved = _get(n, channel_id, project)
    if body.kind != saved["kind"]:
        raise HTTPException(400, "a channel's kind can't change; add a new channel")
    ch = await _clean(body, saved["project"], saved)
    n.store.update(channel_id, ch)
    changed = sorted(body.secrets)
    store.audit(
        session.user.username,
        "notify.update",
        _target(ch),
        detail={"secrets_changed": changed} if changed else None,
    )
    return view(n, _get(n, channel_id, project))


def _delete(
    services: Services, store: Store, session: Session, project: str | None, channel_id: int
) -> None:
    n = _notifier(services)
    ch = _get(n, channel_id, project)
    n.store.delete(channel_id)
    store.audit(session.user.username, "notify.delete", _target(ch))


async def _test(
    services: Services, store: Store, session: Session, project: str | None, channel_id: int
) -> dict[str, Any]:
    n = _notifier(services)
    ch = _get(n, channel_id, project)
    try:
        await n.test(ch)
    except kinds.NotifyError as e:
        store.audit(session.user.username, "notify.test", _target(ch), ok=False)
        return {"ok": False, "error": str(e) or type(e).__name__}
    store.audit(session.user.username, "notify.test", _target(ch))
    return {"ok": True}


def _deliveries(services: Services, project: str | None, channel_id: int) -> list[dict[str, Any]]:
    n = _notifier(services)
    _get(n, channel_id, project)
    return [
        {
            "id": d["id"],
            "status": d["status"],
            "attempts": d["attempts"],
            "created_at": d["created_at"],
            "sent_at": d["sent_at"],
            "error": d["error"],
            "events": [
                {
                    "event": e["event"],
                    "severity": e["alert"]["severity"],
                    "title": e["alert"]["title"],
                }
                for e in d["events"]
            ],
        }
        for d in n.store.deliveries(channel_id)
    ]


async def _telegram(
    services: Services, project: str | None, body: TelegramChats
) -> list[dict[str, Any]]:
    n = _notifier(services)
    token = body.token
    if body.channel_id is not None:
        ch = _get(n, body.channel_id, project)
        token = ch["secrets"].get("token") if ch["kind"] == "telegram" else None
    if not token:
        raise HTTPException(400, "a bot token is needed")
    try:
        return await kinds.telegram_chats(n.http, token)
    except (kinds.NotifyError, httpx.HTTPError) as e:
        raise HTTPException(502, f"Telegram: {e}") from e


# --- routes -----------------------------------------------------------------------------


@router.get("/notify/kinds")
async def channel_kinds(_: ScopeDep) -> dict[str, Any]:
    """What each kind of channel needs, for the form."""
    return {
        name: {
            "label": k.label,
            "config": list(k.config),
            "secrets": list(k.secrets),
            "required": list(k.required),
            "defaults": k.defaults,
        }
        for name, k in KINDS.items()
    }


@router.get("/notify/channels")
async def cluster_channels(_: AdminDep, services: ServicesDep) -> list[dict[str, Any]]:
    """Every channel, the projects' included (a cluster admin's overview)."""
    return _list(services, None)


@router.post("/notify/channels", status_code=201)
async def add_cluster_channel(
    body: ChannelIn, session: AdminDep, services: ServicesDep, store: StoreDep
) -> dict[str, Any]:
    return await _create(services, store, session, None, body)


@router.put("/notify/channels/{channel_id}")
async def change_channel(
    channel_id: ChannelId,
    body: ChannelIn,
    session: AdminDep,
    services: ServicesDep,
    store: StoreDep,
) -> dict[str, Any]:
    return await _update(services, store, session, None, channel_id, body)


@router.delete("/notify/channels/{channel_id}", status_code=204)
async def remove_channel(
    channel_id: ChannelId, session: AdminDep, services: ServicesDep, store: StoreDep
) -> None:
    _delete(services, store, session, None, channel_id)


@router.post("/notify/channels/{channel_id}/test")
async def test_channel(
    channel_id: ChannelId, session: AdminDep, services: ServicesDep, store: StoreDep
) -> dict[str, Any]:
    return await _test(services, store, session, None, channel_id)


@router.get("/notify/channels/{channel_id}/deliveries")
async def channel_deliveries(
    channel_id: ChannelId, _: AdminDep, services: ServicesDep
) -> list[dict[str, Any]]:
    return _deliveries(services, None, channel_id)


@router.post("/notify/telegram-chats")
async def telegram_chats(
    body: TelegramChats, _: AdminDep, services: ServicesDep
) -> list[dict[str, Any]]:
    return await _telegram(services, None, body)


@router.get("/projects/{project}/notify/channels")
async def project_channels(
    project: ProjectName, _: ProjectManageDep, services: ServicesDep
) -> list[dict[str, Any]]:
    return _list(services, project)


@router.post("/projects/{project}/notify/channels", status_code=201)
async def add_project_channel(
    project: ProjectName,
    body: ChannelIn,
    session: ProjectManageDep,
    services: ServicesDep,
    store: StoreDep,
) -> dict[str, Any]:
    return await _create(services, store, session, project, body)


@router.put("/projects/{project}/notify/channels/{channel_id}")
async def change_project_channel(
    project: ProjectName,
    channel_id: ChannelId,
    body: ChannelIn,
    session: ProjectManageDep,
    services: ServicesDep,
    store: StoreDep,
) -> dict[str, Any]:
    return await _update(services, store, session, project, channel_id, body)


@router.delete("/projects/{project}/notify/channels/{channel_id}", status_code=204)
async def remove_project_channel(
    project: ProjectName,
    channel_id: ChannelId,
    session: ProjectManageDep,
    services: ServicesDep,
    store: StoreDep,
) -> None:
    _delete(services, store, session, project, channel_id)


@router.post("/projects/{project}/notify/channels/{channel_id}/test")
async def test_project_channel(
    project: ProjectName,
    channel_id: ChannelId,
    session: ProjectManageDep,
    services: ServicesDep,
    store: StoreDep,
) -> dict[str, Any]:
    return await _test(services, store, session, project, channel_id)


@router.get("/projects/{project}/notify/channels/{channel_id}/deliveries")
async def project_channel_deliveries(
    project: ProjectName, channel_id: ChannelId, _: ProjectManageDep, services: ServicesDep
) -> list[dict[str, Any]]:
    return _deliveries(services, project, channel_id)


@router.post("/projects/{project}/notify/telegram-chats")
async def project_telegram_chats(
    project: ProjectName, body: TelegramChats, _: ProjectManageDep, services: ServicesDep
) -> list[dict[str, Any]]:
    return await _telegram(services, project, body)
