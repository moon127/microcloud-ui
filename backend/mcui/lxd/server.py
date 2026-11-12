"""Server info, feature flags (CF-3) and cluster member discovery."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from mcui.lxd.client import LXDClient

# UI feature -> LXD api_extensions that must all be present (D19).
FEATURES: dict[str, tuple[str, ...]] = {
    "clustering": ("clustering",),
    "metrics": ("metrics",),
    "vga_console": ("console_vga_type",),
    "network_acls": ("network_acl",),
    "network_forwards": ("network_forward",),
    "network_load_balancers": ("network_load_balancer",),
    "network_peers": ("network_peer",),
    "network_zones": ("network_dns",),
    "storage_buckets": ("storage_buckets",),
    "fine_grained_auth": ("access_management",),
    "cluster_evacuation": ("clustering_evacuation",),
}


# LTS series: end of support (LXD "Releases and snaps") and the first release fixing the
# critical TLS client-certificate authentication bypass (advisory of 2026-10-28)
LTS: dict[str, tuple[str, tuple[int, ...]]] = {
    "5.0": ("June 2027", (5, 0, 11)),
    "5.21": ("June 2029", (5, 21, 9)),
    "6.10": ("June 2031", (6, 10, 1)),
}


def release(version: str) -> tuple[str, str | None]:
    """How to describe an LXD version, and a warning if it misses a security fix."""
    nums = tuple(int(n) for n in re.findall(r"\d+", version.split("-")[0])[:3])
    if len(nums) < 2:
        return "unknown release", None
    series = f"{nums[0]}.{nums[1]}"
    if series not in LTS:
        return "feature release", None
    until, fixed = LTS[series]
    label = f"LTS {series}, supported until {until}"
    if nums + (0,) * (3 - len(nums)) < fixed:
        need = ".".join(map(str, fixed))
        return (
            label,
            "misses a critical security fix (TLS client authentication): "
            f"update to {need} or later (snap refresh lxd)",
        )
    return label, None


# API changes the UI adapts to rather than hides (Phase 5): either way works, so `mcui check`
# doesn't list them as unavailable
ADAPTS: dict[str, tuple[str, ...]] = {
    # LXD 6.8+: CephFS pools take cephfs.path; source is refused
    "cephfs_path": ("storage_remote_drop_source",),
    # LXD 6.9+: evacuation refuses to break quorum unless forced
    "evacuation_force": ("clustering_evacuation_force",),
}


def features(api_extensions: list[str]) -> dict[str, bool]:
    have = set(api_extensions)
    return {name: all(x in have for x in needs) for name, needs in {**FEATURES, **ADAPTS}.items()}


@dataclass
class ServerInfo:
    version: str
    clustered: bool
    server_name: str
    auth: str
    auth_method: str | None
    api_extensions: list[str] = field(default_factory=list)

    @classmethod
    def from_metadata(cls, md: dict[str, Any]) -> ServerInfo:
        env = md.get("environment") or {}
        return cls(
            version=str(env.get("server_version", "")),
            clustered=bool(env.get("server_clustered", False)),
            server_name=str(env.get("server_name", "")),
            auth=str(md.get("auth", "")),
            auth_method=md.get("auth_user_method"),
            api_extensions=list(md.get("api_extensions") or []),
        )

    @property
    def trusted(self) -> bool:
        return self.auth == "trusted"

    def features(self) -> dict[str, bool]:
        return features(self.api_extensions)

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "clustered": self.clustered,
            "server_name": self.server_name,
            "auth": self.auth,
            "auth_method": self.auth_method,
            "features": self.features(),
        }


@dataclass
class Member:
    name: str
    url: str
    status: str
    roles: list[str] = field(default_factory=list)
    failure_domain: str = ""
    message: str = ""
    architecture: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "url": self.url,
            "status": self.status,
            "roles": self.roles,
            "failure_domain": self.failure_domain,
            "message": self.message,
            "architecture": self.architecture,
        }


async def server_info(client: LXDClient) -> ServerInfo:
    return ServerInfo.from_metadata(await client.get("/1.0"))


async def members(client: LXDClient, info: ServerInfo | None = None) -> list[Member]:
    """Cluster members, or the single server itself when not clustered."""
    info = info or await server_info(client)
    if not info.clustered:
        return [Member(name=info.server_name, url=client.endpoint, status="Online")]
    data = await client.get("/1.0/cluster/members", recursion=1)
    return [
        Member(
            name=m["server_name"],
            url=m["url"],
            status=m.get("status", ""),
            roles=list(m.get("roles") or []),
            failure_domain=m.get("failure_domain", ""),
            message=m.get("message", ""),
            architecture=m.get("architecture", ""),
        )
        for m in data
    ]
