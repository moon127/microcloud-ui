"""Configuration: ``config.toml`` plus ``MCUI_<SECTION>__<KEY>`` env overrides (CF-1)."""

from __future__ import annotations

import os
import stat
import tomllib
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

CONFIG_ENV = "MCUI_CONFIG"
# used when neither a path nor $MCUI_CONFIG is given and it exists (development, D71);
# production always names its config (-c /etc/mcui/config.toml, or MCUI_CONFIG)
DEFAULT_CONFIG = Path("~/.config/mcui/config.toml")


class ConfigError(Exception):
    """The configuration is missing, malformed or unsafe."""


def _expand(p: Path) -> Path:
    return p.expanduser()


ExpandedPath = Annotated[Path, AfterValidator(_expand)]


def check_key_file(path: Path) -> None:
    """Refuse private keys that are missing or readable by group/other (like OpenSSH)."""
    try:
        mode = path.stat().st_mode
    except OSError as e:
        raise ValueError(f"cannot read key file {path}: {e.strerror}") from e
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ValueError(
            f"key file {path} is accessible by group/other "
            f"(mode {stat.S_IMODE(mode):o}); run: chmod 600 {path}"
        )


def _check_readable(path: Path) -> None:
    if not path.is_file():
        raise ValueError(f"file not found: {path}")


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ServerSettings(_Section):
    data_dir: ExpandedPath = Path("./data")
    secure_cookies: bool = True
    session_hours: int = Field(default=12, ge=1)
    # where `mcui serve` listens (its --host/--port override these)
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    # serve HTTPS directly (production without a reverse proxy, docs/DEPLOY.md)
    tls_cert: ExpandedPath | None = None
    tls_key: ExpandedPath | None = None
    # behind a reverse proxy (docs/DEPLOY.md §10, D65): the address browsers use, accepted
    # as a websocket Origin even when the proxy rewrites Host or drops its port
    public_url: str | None = None
    # proxies whose X-Forwarded-For/-Proto are trusted for the logged client address
    # (comma-separated IPs or CIDRs, or "*"); unset: uvicorn's default, 127.0.0.1
    forwarded_allow_ips: str | None = None
    # the largest upload (image, ISO, backup, instance file) streamed to LXD; 0: no limit.
    # Image uploads land on a member's root filesystem, outside project limits (D90)
    max_upload_bytes: int = Field(default=16 * 2**30, ge=0)

    @field_validator("public_url")
    @classmethod
    def _public_url(cls, v: str | None) -> str | None:
        if v is None:
            return None
        u = urlsplit(v)
        if u.scheme not in ("http", "https") or not u.hostname or u.path not in ("", "/"):
            raise ValueError("public_url must be like https://mcui.example.org[:port]")
        return f"{u.scheme}://{u.netloc.lower()}"

    @model_validator(mode="after")
    def _tls(self) -> ServerSettings:
        if (self.tls_cert is None) != (self.tls_key is None):
            raise ValueError("server tls_cert and tls_key must be set together")
        if self.tls_cert is not None and self.tls_key is not None:
            _check_readable(self.tls_cert)
            check_key_file(self.tls_key)
        return self


class LXDSettings(_Section):
    endpoints: list[str] = Field(min_length=1)
    server_cert: ExpandedPath
    server_name: str | None = None
    client_cert: ExpandedPath
    client_key: ExpandedPath
    timeout_seconds: float = Field(default=10.0, gt=0)

    @field_validator("endpoints")
    @classmethod
    def _https_only(cls, v: list[str]) -> list[str]:
        for e in v:
            if not e.startswith("https://"):
                raise ValueError(f"endpoint must be https://: {e}")
        return [e.rstrip("/") for e in v]

    @model_validator(mode="after")
    def _files(self) -> LXDSettings:
        _check_readable(self.server_cert)
        _check_readable(self.client_cert)
        check_key_file(self.client_key)
        return self


class MetricsSettings(_Section):
    cert: ExpandedPath | None = None
    key: ExpandedPath | None = None
    port: int = Field(default=8443, ge=1, le=65535)
    interval_seconds: int = Field(default=15, ge=10)
    store: Literal["sqlite"] = "sqlite"
    member_overrides: dict[str, str] = Field(default_factory=dict)
    fallback_to_api_port: bool = False
    collect: bool = True  # background collection + history (Phase 2)
    state_interval_seconds: int = Field(default=60, ge=15)
    retention_raw_hours: int = Field(default=24, ge=1)
    retention_1m_days: int = Field(default=14, ge=1)
    retention_1h_days: int = Field(default=365, ge=1)

    @model_validator(mode="after")
    def _pair(self) -> MetricsSettings:
        if (self.cert is None) != (self.key is None):
            raise ValueError("metrics cert and key must be set together")
        if self.cert is not None and self.key is not None:
            _check_readable(self.cert)
            check_key_file(self.key)
        return self


class CephSettings(_Section):
    enabled: bool = True
    # empty: http://<each member>:<mgr_port>; the active mgr is whichever has data
    mgr_endpoints: list[str] = Field(default_factory=list)
    mgr_port: int = Field(default=9283, ge=1, le=65535)
    timeout_seconds: float = Field(default=10.0, gt=0)


class NodeExporterSettings(_Section):
    """Optional host metrics (MO-12, D58): ``<scheme>://<member host>:<port>/metrics``."""

    enabled: bool = True
    scheme: Literal["http", "https"] = "https"
    port: int = Field(default=9100, ge=1, le=65535)
    # CA (or the self-signed cert itself) to verify https; empty: system trust store
    ca: ExpandedPath | None = None
    # name checked against the certificate instead of the member's address
    server_name: str | None = None
    username: str | None = None
    password_file: ExpandedPath | None = None
    member_overrides: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=5.0, gt=0)

    @model_validator(mode="after")
    def _files(self) -> NodeExporterSettings:
        if (self.username is None) != (self.password_file is None):
            raise ValueError("node_exporter username and password_file must be set together")
        if self.ca is not None:
            _check_readable(self.ca)
        if self.password_file is not None:
            check_key_file(self.password_file)
        return self

    def password(self) -> str | None:
        if self.password_file is None:
            return None
        return self.password_file.read_text().strip()


class HostDevicesSettings(_Section):
    """Host paths and USB devices (D86, D87). A cluster admin can change ``hidden_paths``
    and ``browse`` in the app too; the app's saved values then win over these."""

    # folders project admins can't browse or attach; unset: the built-in list
    # (mcui.hostfs.DEFAULT_HIDDEN)
    hidden_paths: list[str] | None = None
    # the folder browser: helper containers, one per member, in their own project
    browse: bool = False
    browse_project: str = Field(default="mcui-server", pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    # empty: the pool of the default project's root disk
    browse_pool: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    # a stable Alpine release (D97): fixes come as backports, and images.lxd.canonical.com
    # rebuilds it daily. The weekly security check says when a newer release is out
    browse_image: str = "alpine/3.24"
    # a stopped helper older than this is made again from the current image when next
    # used, so its packages stay current (it has no network to update itself; D97)
    browse_refresh_days: int = Field(default=7, ge=1, le=90)
    # a helper is stopped once nobody has browsed with it for this long
    browse_idle_minutes: int = Field(default=10, ge=1)

    @field_validator("hidden_paths")
    @classmethod
    def _absolute(cls, v: list[str] | None) -> list[str] | None:
        if v is not None and any(not p.startswith("/") for p in v):
            raise ValueError("hidden_paths are absolute paths")
        return v


# (warning, critical) per threshold rule (D91); percentages, except cert_days (days left,
# so lower is worse)
DEFAULT_THRESHOLDS: dict[str, tuple[float, float]] = {
    "ceph_raw": (75, 85),
    "ceph_pool": (80, 90),
    "local_pool": (80, 90),
    "host_fs": (85, 95),
    "host_memory": (90, 95),
    "instance_rootfs": (90, 95),
    "project_limits": (90, 98),
    "ovn_uplink": (80, 95),
    "network_addresses": (80, 95),
    "cert_days": (30, 7),
}


def threshold_problem(name: str, pair: tuple[float, float]) -> str | None:
    """Why a [warning, critical] pair can't be used, if so (D91 review): an empty field
    sent as 0 would alert on everything."""
    warn, crit = pair
    if any(v != v or v in (float("inf"), float("-inf")) for v in pair):
        return "numbers only"
    if name == "cert_days":
        if not 0 < crit < warn <= 3650:
            return "days left: critical below warning, both above 0"
        return None
    if not 0 < warn < crit <= 100:
        return "percent: warning below critical, both between 0 and 100"
    return None


class AlertSettings(_Section):
    """Alerts (Phase 8a, D91). A cluster admin can change thresholds and switch rules off
    on the Settings page too; values saved there win over these."""

    enabled: bool = True
    interval_seconds: int = Field(default=30, ge=10)
    retention_days: int = Field(default=90, ge=1)
    # rule name -> [warning, critical]; unset rules keep DEFAULT_THRESHOLDS
    thresholds: dict[str, tuple[float, float]] = Field(default_factory=dict)
    disabled: list[str] = Field(default_factory=list)

    @field_validator("thresholds")
    @classmethod
    def _known(cls, v: dict[str, tuple[float, float]]) -> dict[str, tuple[float, float]]:
        unknown = sorted(set(v) - set(DEFAULT_THRESHOLDS))
        if unknown:
            raise ValueError(f"unknown alert thresholds: {', '.join(unknown)}")
        for name, pair in v.items():
            problem = threshold_problem(name, pair)
            if problem:
                raise ValueError(f"alerts threshold {name}: {problem}")
        return v

    @field_validator("disabled")
    @classmethod
    def _known_rules(cls, v: list[str]) -> list[str]:
        from mcui.alerts.rules import RULES

        unknown = sorted(set(v) - set(RULES))
        if unknown:
            raise ValueError(f"unknown alert rules: {', '.join(unknown)}")
        return v


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MCUI_", env_nested_delimiter="__", extra="forbid")

    server: ServerSettings = Field(default_factory=lambda: ServerSettings())
    lxd: LXDSettings | None = None
    metrics: MetricsSettings = Field(default_factory=lambda: MetricsSettings())
    ceph: CephSettings = Field(default_factory=lambda: CephSettings())
    node_exporter: NodeExporterSettings = Field(default_factory=lambda: NodeExporterSettings())
    host_devices: HostDevicesSettings = Field(default_factory=lambda: HostDevicesSettings())
    alerts: AlertSettings = Field(default_factory=lambda: AlertSettings())

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # env beats the TOML file (passed as init kwargs)
        return env_settings, init_settings

    @property
    def metrics_cert(self) -> tuple[Path, Path] | None:
        """Cert/key used for scraping: the metrics pair, else the management pair."""
        if self.metrics.cert is not None and self.metrics.key is not None:
            return self.metrics.cert, self.metrics.key
        if self.lxd is not None:
            return self.lxd.client_cert, self.lxd.client_key
        return None


def config_path(path: Path | str | None = None) -> Path | None:
    """The config file to read: ``path``, else ``$MCUI_CONFIG``, else ``DEFAULT_CONFIG`` if
    it exists, else none (built-in defaults: no cluster, data in ./data)."""
    if path is None:
        path = os.environ.get(CONFIG_ENV) or None
    if path is not None:
        return Path(path).expanduser()
    default = DEFAULT_CONFIG.expanduser()
    return default if default.is_file() else None


def load_settings(path: Path | str | None = None) -> Settings:
    """Load settings from ``config_path(path)``; env vars override."""
    p = config_path(path)
    data: dict[str, Any] = {}
    if p is not None:
        try:
            data = tomllib.loads(p.read_text())
        except OSError as e:
            raise ConfigError(f"cannot read config {p}: {e.strerror}") from e
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"invalid TOML in {p}: {e}") from e
    try:
        return Settings(**data)
    except ValueError as e:
        raise ConfigError(str(e)) from e
