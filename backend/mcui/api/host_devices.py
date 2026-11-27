"""Host paths and USB devices on an instance (D86): what its member has, add, remove.

These reach the host, so they need the host-devices right: cluster admins, and project
admins a cluster admin gave it (``mcui.auth.policy.may_use_host_devices``). In a
restricted project LXD also wants ``restricted.devices.disk=allow`` (better with
``restricted.devices.disk.paths``) or ``restricted.devices.usb=allow``. Both kinds can be
added and removed while the instance runs. LXD's "auto" evacuation stops such an instance
instead of moving it; ``cluster.evacuate=migrate`` moves it anyway, for a path that exists
on every member (NFS, a SAN).

To find a path (D87): the member's mounted filesystems from its node_exporter, and, when a
cluster admin switched it on, a folder browser (``mcui.lxd.hostbrowser``). Project admins
don't see or attach the hidden folders (``mcui.hostfs``), and with
``restricted.devices.disk.paths`` set they browse only inside those.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel, Field, field_validator

from mcui.api.audited import audited
from mcui.api.deps import (
    HiddenPathsDep,
    LXDDep,
    ProjectHostDevicesDep,
    ServicesDep,
    StoreDep,
    check_host_devices,
)
from mcui.api.instances import InstName, ProjectName
from mcui.auth.policy import is_host_device
from mcui.auth.store import Session
from mcui.hostfs import hidden, normalize, under
from mcui.lxd import operations
from mcui.lxd.client import LXDClient, LXDError
from mcui.lxd.hostbrowser import MAX_ENTRIES, BrowseError
from mcui.metrics.live import SKIP_FSTYPES
from mcui.metrics.select import NODE_SKIP_MOUNT_PREFIXES
from mcui.services import Services

router = APIRouter(prefix="/api/instances")

DEVICE = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
DeviceName = Annotated[str, Path(pattern=DEVICE, max_length=63)]
USB_ID = r"^[0-9a-fA-F]{4}$"
MASS_STORAGE = 8  # the USB interface class
WRITABLE = ("architecture", "config", "devices", "ephemeral", "profiles", "stateful", "description")


def _path(v: str) -> str:
    if not v.startswith("/") or "\0" in v or ".." in v.split("/"):
        raise ValueError("an absolute path, without ..")
    return v


class HostPath(BaseModel):
    kind: Literal["path"]
    name: str = Field(pattern=DEVICE, max_length=63)
    source: str = Field(max_length=4096)
    # in the instance; a VM's block device has none
    path: str | None = Field(default=None, max_length=4096)
    readonly: bool = True
    shift: bool = False
    # every member has the path (NFS, a SAN): evacuation moves the instance (D86)
    everywhere: bool = False

    _source = field_validator("source")(_path)

    @field_validator("path")
    @classmethod
    def _in(cls, v: str | None) -> str | None:
        return None if v is None else _path(v)


class Usb(BaseModel):
    kind: Literal["usb"]
    name: str = Field(pattern=DEVICE, max_length=63)
    vendorid: str = Field(pattern=USB_ID)
    productid: str = Field(pattern=USB_ID)
    serial: str | None = Field(default=None, max_length=255)
    # to tell identical devices without a serial apart
    busnum: int | None = Field(default=None, ge=0)
    devnum: int | None = Field(default=None, ge=0)


HostDevice = Annotated[HostPath | Usb, Field(discriminator="kind")]


def device_of(body: HostPath | Usb) -> dict[str, str]:
    if isinstance(body, Usb):
        dev = {
            "type": "usb",
            "vendorid": body.vendorid.lower(),
            "productid": body.productid.lower(),
        }
        if body.serial:
            dev["serial"] = body.serial
        if body.busnum is not None and body.devnum is not None:
            dev |= {"busnum": str(body.busnum), "devnum": str(body.devnum)}
        return dev
    dev = {"type": "disk", "source": body.source}
    if body.path:
        dev["path"] = body.path
    if body.readonly:
        dev["readonly"] = "true"
    if body.shift:
        dev["shift"] = "true"
    return dev


def _usb(d: dict[str, Any]) -> dict[str, Any]:
    ifaces = d.get("interfaces") or []
    return {
        "vendor_id": d.get("vendor_id", ""),
        "product_id": d.get("product_id", ""),
        "vendor": d.get("vendor", ""),
        "product": d.get("product", ""),
        "serial": d.get("serial", ""),
        "busnum": d.get("bus_address"),
        "devnum": d.get("device_address"),
        "storage": any(i.get("class_id") == MASS_STORAGE for i in ifaces),
        "drivers": sorted({i["driver"] for i in ifaces if i.get("driver")}),
    }


def _disk(d: dict[str, Any]) -> dict[str, Any]:
    by_id = f"/dev/disk/by-id/{d['device_id']}" if d.get("device_id") else f"/dev/{d['id']}"
    return {
        "id": d.get("id", ""),
        "model": d.get("model", ""),
        "size": d.get("size", 0),
        "source": by_id,
        "partitions": [
            {
                "id": p.get("id", ""),
                "size": p.get("size", 0),
                "source": f"{by_id}-part{p['partition']}",
            }
            for p in d.get("partitions") or []
            if p.get("partition")
        ],
    }


def mounts(services: Services, member: str) -> list[dict[str, Any]]:
    """The member's real filesystems, from its last node_exporter scrape (not ``/``)."""
    scrape = services.node.last.get(member) if services.node else None
    if scrape is None or not scrape.ok:
        return []
    seen: dict[str, dict[str, Any]] = {}
    for smp in scrape.exposition.samples:
        if smp.name != "node_filesystem_size_bytes":
            continue
        mp, fstype = smp.labels.get("mountpoint", ""), smp.labels.get("fstype", "")
        if mp in ("", "/") or fstype in SKIP_FSTYPES or mp.startswith(NODE_SKIP_MOUNT_PREFIXES):
            continue
        seen[mp] = {"path": mp, "fstype": fstype, "size": smp.value}
    return sorted(seen.values(), key=lambda m: m["path"])


async def allowed_paths(lxd: LXDClient, session: Session, project: str) -> list[str]:
    """A restricted project's ``restricted.devices.disk.paths``: where a project admin may
    browse (LXD refuses attaching anything else). Empty: anywhere not hidden."""
    if session.user.is_admin:
        return []
    cfg = (await lxd.get(f"/1.0/projects/{project}")).get("config") or {}
    if cfg.get("restricted") != "true":
        return []
    raw = cfg.get("restricted.devices.disk.paths", "")
    return [normalize(p.strip()) for p in raw.split(",") if p.strip().startswith("/")]


def _member(inst: dict[str, Any]) -> str:
    loc = inst.get("location") or ""
    return "" if loc == "none" else loc


@router.get("/{project}/{name}/host-devices")
async def host_inventory(
    project: ProjectName,
    name: InstName,
    session: ProjectHostDevicesDep,
    lxd: LXDDep,
    services: ServicesDep,
) -> dict[str, Any]:
    """The USB devices, USB disks and mounted filesystems of the instance's member, and
    whether the folder browser is on."""
    inst = await lxd.get(f"/1.0/instances/{name}", project=project)
    member = _member(inst)
    res = await lxd.get("/1.0/resources", **({"target": member} if member else {}))
    disks = (res.get("storage") or {}).get("disks") or []
    fs = services.hostfs()
    hide = [] if session.user.is_admin else fs.hidden_paths
    allowed = await allowed_paths(lxd, session, project)
    return {
        "member": member,
        "usb": [_usb(d) for d in (res.get("usb") or {}).get("devices") or []],
        "disks": [
            d
            for d in (_disk(d) for d in disks if d.get("type") == "usb")
            if not hidden(d["source"], hide)  # /dev/sdX without a by-id path (D90)
        ],
        "mounts": [
            m
            for m in mounts(services, member)
            if not hidden(m["path"], hide) and (not allowed or under(m["path"], allowed))
        ],
        "browse": fs.browse and services.browser is not None,
        "hidden": hide,
        "allowed": allowed,
    }


@router.get("/{project}/{name}/host-files")
async def host_files(
    project: ProjectName,
    name: InstName,
    session: ProjectHostDevicesDep,
    lxd: LXDDep,
    services: ServicesDep,
    path: str = Query(default="/", max_length=4096),
) -> dict[str, Any]:
    """A folder on the instance's member, through the folder browser (D87)."""
    fs = services.hostfs()
    if not fs.browse or services.browser is None:
        raise HTTPException(409, "the folder browser is off (Settings → Host devices)")
    try:
        p = normalize(path)
    except ValueError as e:
        raise HTTPException(422, "an absolute path") from e
    hide = [] if session.user.is_admin else fs.hidden_paths
    allowed = await allowed_paths(lxd, session, project)
    if allowed and not under(p, allowed):
        raise HTTPException(403, f"{project} allows host paths only under {', '.join(allowed)}")
    if hidden(p, hide):
        raise HTTPException(403, f"{p} is hidden from project admins")
    member = _member(await lxd.get(f"/1.0/instances/{name}", project=project))
    mounted = {m["path"]: m["fstype"] for m in mounts(services, member)}
    try:
        if not session.user.is_admin:
            # the same checks on the path with its links resolved (D90)
            real = await services.browser.resolve(member, p, list(mounted))
            if (allowed and not under(real, allowed)) or hidden(real, hide):
                raise HTTPException(403, f"{p} leads to a folder hidden from project admins")
        entries = await services.browser.listing(member, p, list(mounted))
    except BrowseError as e:
        raise HTTPException(e.status, str(e)) from e
    services.store.audit(
        session.user.username,
        "host.browse",
        f"{member or 'host'}:{p}",
        detail={"instance": f"{project}/{name}"},
    )
    out: list[dict[str, Any]] = []
    for ent in entries:
        full = p.rstrip("/") + "/" + ent.name
        if hidden(full, hide):
            continue
        out.append({**ent.__dict__, **({"mount": mounted[full]} if full in mounted else {})})
    return {
        "member": member,
        "path": p,
        "entries": out[:MAX_ENTRIES],
        "truncated": len(out) > MAX_ENTRIES,
    }


@router.post("/{project}/{name}/host-devices")
async def add_host_device(
    project: ProjectName,
    name: InstName,
    body: HostDevice,
    session: ProjectHostDevicesDep,
    store: StoreDep,
    lxd: LXDDep,
    hidden_paths: HiddenPathsDep,
) -> dict[str, Any]:
    check_host_devices(session, project, {body.name: device_of(body)}, None, hidden_paths)
    inst = await lxd.get(f"/1.0/instances/{name}", project=project)
    if body.name in (inst.get("expanded_devices") or inst.get("devices") or {}):
        raise HTTPException(409, f"{name} already has a device {body.name}")
    if isinstance(body, HostPath) and not body.path and inst.get("type") != "virtual-machine":
        raise HTTPException(400, "a container needs a path to mount it at")
    patch: dict[str, Any] = {"devices": {body.name: device_of(body)}}
    if isinstance(body, HostPath) and body.everywhere:
        patch["config"] = {"cluster.evacuate": "migrate"}
    st = await audited(
        session,
        store,
        "instance.host-device.add",
        f"{project}/{name}",
        operations.start(
            lxd, "PATCH", f"/1.0/instances/{name}", json=patch, params={"project": project}
        ),
        detail={"device": body.name, **patch["devices"][body.name], **patch.get("config", {})},
    )
    return st.as_dict()


async def _remove(lxd: LXDClient, project: str, name: str, device: str) -> operations.Started:
    path = f"/1.0/instances/{name}"
    r = await lxd.raw("GET", path, params={"project": project})
    if r.status_code != 200:
        raise LXDError(r.json().get("error") or f"HTTP {r.status_code}", r.status_code)
    inst = r.json()["metadata"]
    devices = dict(inst.get("devices") or {})
    if not is_host_device(devices.get(device) or {}):
        raise HTTPException(
            409, f"{device} on {name} is not a host device (it may come from a profile)"
        )
    del devices[device]
    put = {k: inst[k] for k in WRITABLE if k in inst} | {"devices": devices}
    etag = r.headers.get("etag")
    return await operations.start(
        lxd,
        "PUT",
        path,
        json=put,
        params={"project": project},
        headers={"If-Match": etag} if etag else None,
    )


@router.delete("/{project}/{name}/host-devices/{device}")
async def remove_host_device(
    project: ProjectName,
    name: InstName,
    device: DeviceName,
    session: ProjectHostDevicesDep,
    store: StoreDep,
    lxd: LXDDep,
) -> dict[str, Any]:
    st = await audited(
        session,
        store,
        "instance.host-device.remove",
        f"{project}/{name}",
        _remove(lxd, project, name, device),
        detail={"device": device},
    )
    return st.as_dict()
