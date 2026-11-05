"""Login, logout, current user (AU-1) and user management (AU-2)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from mcui.api.deps import COOKIE, AdminDep, ServicesDep, SessionDep, StoreDep
from mcui.auth.store import AuthError, Role, Session, User

router = APIRouter(prefix="/api")


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


class NewUser(LoginBody):
    role: Role = "viewer"


class UserChange(BaseModel):
    role: Role | None = None
    password: str | None = Field(default=None, min_length=1, max_length=1024)


def _user(u: User) -> dict[str, Any]:
    return {"username": u.username, "role": u.role}


def _me(s: Session) -> dict[str, Any]:
    return {"user": _user(s.user), "csrf": s.csrf}


@router.post("/auth/login")
async def login(body: LoginBody, response: Response, services: ServicesDep) -> dict[str, Any]:
    store = services.store
    user = store.authenticate(body.username, body.password)
    if user is None:
        store.audit(body.username, "auth.login", ok=False)
        raise HTTPException(status_code=401, detail="invalid username or password")
    ttl = services.settings.server.session_hours * 3600
    session = store.create_session(user, ttl)
    response.set_cookie(
        COOKIE,
        session.token,
        max_age=ttl,
        httponly=True,
        samesite="strict",
        secure=services.settings.server.secure_cookies,
        path="/",
    )
    store.audit(user.username, "auth.login")
    return _me(session)


@router.post("/auth/logout", status_code=204)
async def logout(session: SessionDep, store: StoreDep, response: Response) -> None:
    store.delete_session(session.token)
    store.audit(session.user.username, "auth.logout")
    response.delete_cookie(COOKIE, path="/")


@router.get("/auth/me")
async def me(session: SessionDep) -> dict[str, Any]:
    return _me(session)


@router.get("/users")
async def list_users(_: AdminDep, store: StoreDep) -> list[dict[str, Any]]:
    return [_user(u) for u in store.list_users()]


@router.post("/users", status_code=201)
async def create_user(body: NewUser, session: AdminDep, store: StoreDep) -> dict[str, Any]:
    try:
        user = store.add_user(body.username, body.password, body.role)
    except AuthError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    store.audit(session.user.username, "user.create", body.username, detail={"role": body.role})
    return _user(user)


def _existing(store: StoreDep, username: str) -> User:
    user = store.get_user(username)
    if user is None:
        raise HTTPException(status_code=404, detail="no such user")
    return user


def _keep_an_admin(store: StoreDep, user: User) -> None:
    if user.is_admin and store.count_admins() <= 1:
        raise HTTPException(status_code=409, detail="cannot remove the last admin")


@router.patch("/users/{username}")
async def change_user(
    username: str, body: UserChange, session: AdminDep, store: StoreDep
) -> dict[str, Any]:
    user = _existing(store, username)
    changed: dict[str, Any] = {}
    if body.role is not None and body.role != user.role:
        _keep_an_admin(store, user)
        store.set_role(username, body.role)
        changed["role"] = body.role
    if body.password is not None:
        store.set_password(username, body.password)
        changed["password"] = True
    store.audit(session.user.username, "user.update", username, detail=changed)
    return _user(_existing(store, username))


@router.delete("/users/{username}", status_code=204)
async def delete_user(username: str, session: AdminDep, store: StoreDep) -> None:
    user = _existing(store, username)
    _keep_an_admin(store, user)
    store.delete_user(username)
    store.audit(session.user.username, "user.delete", username)


@router.get("/audit")
async def audit(
    _: AdminDep, store: StoreDep, limit: int = 100, before: int | None = None
) -> list[dict[str, Any]]:
    return store.audit_entries(limit=max(1, min(limit, 1000)), before_id=before)
