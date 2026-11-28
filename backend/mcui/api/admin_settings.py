"""Settings a cluster admin changes in the app instead of the config file (D87).

Host devices: the folders hidden from project admins, and the folder browser switch.
Saved values win over ``[host_devices]`` in the config file; resetting one goes back to
the file. Switching the browser off removes its helpers, image and project.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from mcui.api.deps import AdminDep, ServicesDep
from mcui.hostfs import BROWSE_KEY, DEFAULT_HIDDEN, HIDDEN_KEY, normalize
from mcui.lxd.client import LXDError
from mcui.lxd.hostbrowser import BrowseError
from mcui.services import Services

router = APIRouter(prefix="/api/settings")


class HostDevicesChange(BaseModel):
    # a list replaces the saved one; ``reset_hidden`` goes back to the config file
    hidden_paths: list[str] | None = Field(default=None, max_length=200)
    reset_hidden: bool = False
    browse: bool | None = None
    reset_browse: bool = False


async def _view(services: Services) -> dict[str, Any]:
    fs = services.hostfs()
    cfg = services.settings.host_devices
    browser = None
    if services.browser is not None:
        try:
            browser = await services.browser.status()
        except Exception as e:  # noqa: BLE001 - the page still shows the settings
            browser = {"project": cfg.browse_project, "error": str(e)}
    return {
        "hidden_paths": fs.hidden_paths,
        "hidden_source": fs.hidden_source,
        "default_hidden": list(DEFAULT_HIDDEN),
        "config_hidden": cfg.hidden_paths,
        "browse": fs.browse,
        "browse_source": fs.browse_source,
        "config_browse": cfg.browse,
        "browse_idle_minutes": cfg.browse_idle_minutes,
        "browse_refresh_days": cfg.browse_refresh_days,
        "browser": browser,
    }


@router.get("/host-devices")
async def host_devices(_: AdminDep, services: ServicesDep) -> dict[str, Any]:
    return await _view(services)


@router.put("/host-devices")
async def change_host_devices(
    body: HostDevicesChange, session: AdminDep, services: ServicesDep
) -> dict[str, Any]:
    store = services.store
    detail: dict[str, Any] = {}
    if body.reset_hidden:
        store.set_setting(HIDDEN_KEY, None)
        detail["hidden_paths"] = "reset"
    elif body.hidden_paths is not None:
        try:
            paths = sorted({normalize(p.strip()) for p in body.hidden_paths if p.strip()})
        except ValueError as e:
            raise HTTPException(422, "hidden paths are absolute paths") from e
        store.set_setting(HIDDEN_KEY, paths)
        detail["hidden_paths"] = paths
    was = services.hostfs().browse
    if body.reset_browse:
        store.set_setting(BROWSE_KEY, None)
        detail["browse"] = "reset"
    elif body.browse is not None:
        store.set_setting(BROWSE_KEY, body.browse)
        detail["browse"] = body.browse
    now = services.hostfs().browse
    store.audit(session.user.username, "settings.host-devices", None, detail=detail)
    if services.browser is not None and was != now:
        try:
            if now:
                await services.browser.prepare()
                store.audit(session.user.username, "host-browser.enable", services.browser.project)
            elif await services.browser.remove_all():
                store.audit(session.user.username, "host-browser.remove", services.browser.project)
        except (BrowseError, LXDError) as e:
            # switching on failed: back off. Switching off stays off; housekeeping retries
            if now:
                store.set_setting(BROWSE_KEY, was)
            status = e.status if isinstance(e, BrowseError) else 502
            raise HTTPException(status, str(e)) from e
    return await _view(services)


@router.post("/host-devices/browser/rebuild")
async def rebuild_browser(session: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Delete the folder browser's helpers and images now; they're made again from the
    current image when next used (D97)."""
    if services.browser is None:
        raise HTTPException(409, "the folder browser isn't available")
    done, failed = await services.browser.rebuild_all()  # goes on past a member's refusal
    services.store.audit(
        session.user.username,
        "host-browser.rebuild",
        services.browser.project,
        detail={"helpers": done, **({"failed": failed} if failed else {})},
        ok=not failed,
    )
    if failed:
        raise HTTPException(502, "not all rebuilt: " + "; ".join(failed)[:500])
    return await _view(services)
