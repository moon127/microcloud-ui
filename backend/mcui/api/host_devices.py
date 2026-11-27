"""Host paths and USB devices on an instance (D86): what its member has, add, remove.

These reach the host, so they need the host-devices right: cluster admins, and project
admins a cluster admin gave it (``mcui.auth.policy.may_use_host_devices``). In a
restricted project LXD also wants ``restricted.devices.disk=allow`` (better with
``restricted.devices.disk.paths``) or ``restricted.devices.usb=allow``. Both kinds can be
added and removed while the instance runs. LXD's "auto" evacuation stops such an instance
instead of moving it; ``cluster.evacuate=migrate`` moves it anyway, for a path that exists
on every member (NFS, a SAN).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, Field, field_validator

from mcui.api.audited import audited
from mcui.api.deps import LXDDep, ProjectHostDevicesDep, StoreDep
from mcui.api.instances import InstName, ProjectName
from mcui.auth.policy import is_host_device
from mcui.lxd import operations
from mcui.lxd.client import LXDClient, LXDError

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


@router.get("/{project}/{name}/host-devices")
async def host_inventory(
    project: ProjectName, name: InstName, _: ProjectHostDevicesDep, lxd: LXDDep
) -> dict[str, Any]:
    """The USB devices and USB disks of the member the instance is on."""
    inst = await lxd.get(f"/1.0/instances/{name}", project=project)
    member = inst.get("location") or ""
    clustered = member not in ("", "none")
    res = await lxd.get("/1.0/resources", **({"target": member} if clustered else {}))
    disks = (res.get("storage") or {}).get("disks") or []
    return {
        "member": member if clustered else "",
        "usb": [_usb(d) for d in (res.get("usb") or {}).get("devices") or []],
        "disks": [_disk(d) for d in disks if d.get("type") == "usb"],
    }


@router.post("/{project}/{name}/host-devices")
async def add_host_device(
    project: ProjectName,
    name: InstName,
    body: HostDevice,
    session: ProjectHostDevicesDep,
    store: StoreDep,
    lxd: LXDDep,
) -> dict[str, Any]:
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
