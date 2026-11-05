from pathlib import Path

import httpx
import pytest

from mcui import __version__
from mcui.config import Settings
from mcui.main import create_app
from mcui.services import build_services
from tests.conftest import fast_store


@pytest.fixture
def services(tmp_path):
    return build_services(Settings(server={"data_dir": str(tmp_path)}), store=fast_store())


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_health_ok(services):
    async with client(create_app(services=services, static_dir=None)) as c:
        r = await c.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "version": __version__, "lxd_configured": False}


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    (tmp_path / "index.html").write_text("<html>spa</html>")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    return tmp_path


async def test_spa_serves_index_and_assets(dist: Path, services):
    async with client(create_app(services=services, static_dir=dist)) as c:
        assert (await c.get("/")).text == "<html>spa</html>"
        assert (await c.get("/instances/foo")).text == "<html>spa</html>"
        assert (await c.get("/assets/app.js")).text == "console.log(1)"


async def test_spa_does_not_shadow_unknown_api(dist: Path, services):
    async with client(create_app(services=services, static_dir=dist)) as c:
        assert (await c.get("/api/nope")).status_code == 404
        assert (await c.get("/api/health")).status_code == 200


async def test_spa_blocks_path_traversal(dist: Path, services):
    secret = dist.parent / "secret.txt"
    secret.write_text("nope")
    async with client(create_app(services=services, static_dir=dist)) as c:
        r = await c.get("/..%2Fsecret.txt")
    assert "nope" not in r.text
    assert r.text == "<html>spa</html>"


async def test_no_static_dir_means_no_spa(tmp_path: Path, services):
    async with client(create_app(services=services, static_dir=tmp_path)) as c:
        assert (await c.get("/")).status_code == 404


async def test_create_app_from_env_and_lifespan_closes(tmp_path, certs, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f"""[server]
data_dir = "{tmp_path}/data"
[lxd]
endpoints = ["https://192.0.2.1:8443"]
server_cert = "{certs.server_cert}"
client_cert = "{certs.client_cert}"
client_key = "{certs.client_key}"
"""
    )
    monkeypatch.setenv("MCUI_CONFIG", str(cfg))
    app = create_app(static_dir=None)
    svc = app.state.services
    assert svc.lxd is not None and svc.metrics_lxd is not None and svc.live is not None
    async with app.router.lifespan_context(app):
        pass
    assert svc.lxd._http.is_closed and svc.metrics_lxd._http.is_closed
    assert (tmp_path / "data" / "mcui.db").is_file()
