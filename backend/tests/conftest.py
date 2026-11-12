import json
import shutil
import ssl
import subprocess
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from argon2 import PasswordHasher

from mcui.auth.store import Store
from mcui.config import Settings
from mcui.lxd.client import LXDClient

FIXTURES = Path(__file__).parent / "fixtures"
SERVER_NAME = "test-cluster"
EP1 = "https://192.0.2.1:8443"
EP2 = "https://192.0.2.2:8443"


def fixture_json(name: str):
    return json.loads((FIXTURES / "lxd" / name).read_text())


def fixture_text(name: str) -> str:
    return (FIXTURES / "lxd" / name).read_text()


def lxd_ok(metadata, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status, json={"type": "sync", "status": "Success", "status_code": 200, "metadata": metadata}
    )


def lxd_err(message: str, code: int) -> httpx.Response:
    return httpx.Response(code, json={"type": "error", "error": message, "error_code": code})


@pytest.fixture(autouse=True)
def no_default_config(tmp_path_factory, monkeypatch):
    """Never read the developer's own ~/.config/mcui/config.toml (D71)."""
    monkeypatch.setattr(
        "mcui.config.DEFAULT_CONFIG", tmp_path_factory.mktemp("home") / "config.toml"
    )
    monkeypatch.delenv("MCUI_CONFIG", raising=False)


@dataclass
class Certs:
    dir: Path
    server_cert: Path
    server_key: Path
    client_cert: Path
    client_key: Path
    other_cert: Path
    other_key: Path


def _selfsigned(dir: Path, name: str, cn: str, san: str) -> tuple[Path, Path]:
    """An LXD-style cert: self-signed EC P-384 leaf, CA:FALSE, DNS-only SAN."""
    key, crt = dir / f"{name}.key", dir / f"{name}.crt"
    subprocess.run(  # noqa: S603
        ["openssl", "ecparam", "-name", "secp384r1", "-genkey", "-noout", "-out", str(key)],  # noqa: S607
        check=True,
        capture_output=True,
    )
    subprocess.run(  # noqa: S603
        [  # noqa: S607
            "openssl", "req", "-new", "-x509", "-sha384", "-days", "2", "-key", str(key),
            "-subj", f"/O=LXD/CN={cn}", "-out", str(crt),
            "-addext", "basicConstraints=critical,CA:FALSE",
            "-addext", "keyUsage=critical,digitalSignature,keyEncipherment",
            "-addext", "extendedKeyUsage=serverAuth,clientAuth",
            "-addext", f"subjectAltName={san}",
        ],
        check=True,
        capture_output=True,
    )  # fmt: skip
    key.chmod(0o600)
    return crt, key


@pytest.fixture(scope="session")
def certs(tmp_path_factory) -> Certs:
    if shutil.which("openssl") is None:  # pragma: no cover
        pytest.skip("openssl CLI not available")
    d = tmp_path_factory.mktemp("certs")
    sc, sk = _selfsigned(d, "server", "root@test", f"DNS:{SERVER_NAME},IP:127.0.0.1")
    cc, ck = _selfsigned(d, "client", "mcui", "DNS:mcui")
    oc, ok = _selfsigned(d, "other", "root@other", f"DNS:{SERVER_NAME}")
    return Certs(d, sc, sk, cc, ck, oc, ok)


def make_settings(certs: Certs, tmp_path: Path, **overrides) -> Settings:
    data = {
        "server": {"data_dir": str(tmp_path / "data"), "secure_cookies": False},
        "lxd": {
            "endpoints": [EP1, EP2],
            "server_cert": str(certs.server_cert),
            "server_name": SERVER_NAME,
            "client_cert": str(certs.client_cert),
            "client_key": str(certs.client_key),
        },
    }
    for k, v in overrides.items():
        data.setdefault(k, {}).update(v)
    return Settings(**data)


@pytest.fixture
def settings(certs, tmp_path) -> Settings:
    return make_settings(certs, tmp_path)


def fast_store(path=":memory:", **kw) -> Store:
    return Store(path, hasher=PasswordHasher(time_cost=1, memory_cost=8, parallelism=1), **kw)


@pytest.fixture
def store():
    s = fast_store()
    yield s
    s.close()


@pytest.fixture
async def lxd():
    c = LXDClient([EP1, EP2], verify=ssl.create_default_context(), server_name=SERVER_NAME)
    yield c
    await c.aclose()
