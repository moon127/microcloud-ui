"""Alerts: active, history, acknowledge, and their settings (Phase 8a, D91).

Global roles see every alert; a project user sees only alerts about their projects
(their instances' OOM kills and root disks, their project's limits). Acknowledging hides
an alert from the bell and banner until it clears and fires again; a cluster admin can
acknowledge any, a project admin their project's.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from mcui.alerts.rules import RULES
from mcui.alerts.service import DISABLED_KEY, THRESHOLDS_KEY, AlertService
from mcui.api.deps import AdminDep, ScopeDep, ServicesDep, SessionDep
from mcui.auth.policy import allowed
from mcui.config import DEFAULT_THRESHOLDS, threshold_problem

router = APIRouter(prefix="/api")

# for the Settings page: what each rule watches, and the unit of its thresholds
DESCRIPTIONS: dict[str, str] = {
    "ceph_health": "Ceph's own health checks (OSD nearfull, down, degraded, slow...)",
    "ceph_raw": "Raw Ceph capacity used (%)",
    "ceph_pool": "Ceph pools used, as Ceph counts it: copies and the fullest OSD (%)",
    "local_pool": "Local storage pools on each member (%)",
    "host_fs": "Host filesystems, with node_exporter (%)",
    "host_memory": "Host memory in use, with node_exporter (%)",
    "host_clock": "Host clocks synchronised, with node_exporter",
    "member_status": "Cluster members offline or evacuated",
    "lxd_security": "LXD releases missing a security fix",
    "instance_oom": "Instances killing processes for lack of memory",
    "instance_rootfs": "Instance root disks (%)",
    "project_limits": "Project limits.* used (%)",
    "ovn_uplink": "OVN router addresses used from each uplink's range (%)",
    "network_addresses": "IPv4 addresses in use on bridge and OVN networks (%)",
    "cert_expiry": "Certificates expiring (days left)",
    "collection": "Metric collection failing (Ceph mgr, node_exporter, LXD)",
    "notify_failing": "Notification channels failing to send",
}
THRESHOLD_OF = {"cert_expiry": "cert_days"}  # rule -> its threshold, when not its own name


def _alerts(services: ServicesDep) -> AlertService:
    if services.alerts is None:  # pragma: no cover - always built
        raise HTTPException(503, "alerts aren't running")
    return services.alerts


@router.get("/alerts")
async def list_alerts(
    scope: ScopeDep,
    services: ServicesDep,
    state: Literal["active", "history"] = "active",
    limit: int = Query(default=200, ge=1, le=1000),
    before: int | None = None,
) -> list[dict[str, Any]]:
    return _alerts(services).store.list(state == "active", limit, before, scope.projects)


@router.post("/alerts/{alert_id}/ack")
async def acknowledge(
    alert_id: int, _: ScopeDep, session: SessionDep, services: ServicesDep
) -> dict[str, Any]:
    """Policy "scoped": which alert, and so which project, is only known here."""
    alerts = _alerts(services)
    row = alerts.store.get(alert_id)
    user = session.user
    project = row["project"] if row else None
    may = user.is_admin or (project is not None and allowed(user, project, "manage"))
    if row is None or not may:
        raise HTTPException(404, "no such alert")
    if not alerts.store.acknowledge(alert_id, user.username):
        raise HTTPException(409, "the alert has cleared")
    services.store.audit(user.username, "alert.ack", row["key"], detail={"title": row["title"]})
    acked = alerts.store.get(alert_id)
    return acked or {}


class AlertSettingsChange(BaseModel):
    thresholds: dict[str, tuple[float, float]] | None = None
    reset_thresholds: bool = False
    disabled: list[str] | None = Field(default=None, max_length=50)
    reset_disabled: bool = False


def _threshold(rule: str) -> str | None:
    name = THRESHOLD_OF.get(rule, rule)
    return name if name in DEFAULT_THRESHOLDS else None


def _view(services: ServicesDep) -> dict[str, Any]:
    alerts = _alerts(services)
    cfg = services.settings.alerts
    effective = alerts.thresholds()
    return {
        "rules": [
            {
                "name": name,
                "description": DESCRIPTIONS.get(name, name),
                "threshold": _threshold(name),
                "for_seconds": for_s,
            }
            for name, (_, for_s) in RULES.items()
        ],
        "thresholds": {k: list(v) for k, v in effective.items()},
        "default_thresholds": {k: list(v) for k, v in DEFAULT_THRESHOLDS.items()},
        "thresholds_saved": services.store.get_setting(THRESHOLDS_KEY) is not None,
        "disabled": sorted(alerts.disabled()),
        "disabled_saved": services.store.get_setting(DISABLED_KEY) is not None,
        "enabled": cfg.enabled,
        "interval_seconds": cfg.interval_seconds,
        "retention_days": cfg.retention_days,
    }


@router.get("/settings/alerts")
async def alert_settings(_: AdminDep, services: ServicesDep) -> dict[str, Any]:
    return _view(services)


@router.put("/settings/alerts")
async def change_alert_settings(
    body: AlertSettingsChange, session: AdminDep, services: ServicesDep
) -> dict[str, Any]:
    store = services.store
    detail: dict[str, Any] = {}
    if body.reset_thresholds:
        store.set_setting(THRESHOLDS_KEY, None)
        detail["thresholds"] = "reset"
    elif body.thresholds is not None:
        unknown = sorted(set(body.thresholds) - set(DEFAULT_THRESHOLDS))
        if unknown:
            raise HTTPException(422, f"unknown thresholds: {', '.join(unknown)}")
        for name, pair in body.thresholds.items():
            problem = threshold_problem(name, pair)
            if problem:
                raise HTTPException(422, f"{name}: {problem}")
        store.set_setting(THRESHOLDS_KEY, {k: list(v) for k, v in body.thresholds.items()})
        detail["thresholds"] = body.thresholds
    if body.reset_disabled:
        store.set_setting(DISABLED_KEY, None)
        detail["disabled"] = "reset"
    elif body.disabled is not None:
        unknown = sorted(set(body.disabled) - set(RULES))
        if unknown:
            raise HTTPException(422, f"unknown rules: {', '.join(unknown)}")
        store.set_setting(DISABLED_KEY, sorted(set(body.disabled)))
        detail["disabled"] = sorted(set(body.disabled))
    store.audit(session.user.username, "settings.alerts", None, detail=detail)
    return _view(services)
