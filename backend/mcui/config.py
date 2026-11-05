"""Configuration: ``config.toml`` plus ``MCUI_<SECTION>__<KEY>`` env overrides (CF-1)."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on Python 3.10 in CI
    import tomli as tomllib  # type: ignore[import-not-found,unused-ignore]

CONFIG_ENV = "MCUI_CONFIG"


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


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MCUI_", env_nested_delimiter="__", extra="forbid")

    server: ServerSettings = Field(default_factory=lambda: ServerSettings())
    lxd: LXDSettings | None = None
    metrics: MetricsSettings = Field(default_factory=lambda: MetricsSettings())
    ceph: CephSettings = Field(default_factory=lambda: CephSettings())

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


def load_settings(path: Path | str | None = None) -> Settings:
    """Load settings from ``path`` (or ``$MCUI_CONFIG``); env vars override."""
    if path is None:
        path = os.environ.get(CONFIG_ENV) or None
    data: dict[str, Any] = {}
    if path is not None:
        p = Path(path).expanduser()
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
