from pathlib import Path

import httpx
import pytest

from mcui import __version__
from mcui.main import create_app


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_health_ok():
    async with client(create_app(static_dir=None)) as c:
        r = await c.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "version": __version__}


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    (tmp_path / "index.html").write_text("<html>spa</html>")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    return tmp_path


async def test_spa_serves_index_and_assets(dist: Path):
    async with client(create_app(static_dir=dist)) as c:
        assert (await c.get("/")).text == "<html>spa</html>"
        assert (await c.get("/instances/foo")).text == "<html>spa</html>"
        assert (await c.get("/assets/app.js")).text == "console.log(1)"


async def test_spa_does_not_shadow_unknown_api(dist: Path):
    async with client(create_app(static_dir=dist)) as c:
        assert (await c.get("/api/nope")).status_code == 404
        assert (await c.get("/api/health")).status_code == 200


async def test_spa_blocks_path_traversal(dist: Path):
    secret = dist.parent / "secret.txt"
    secret.write_text("nope")
    async with client(create_app(static_dir=dist)) as c:
        r = await c.get("/..%2Fsecret.txt")
    assert "nope" not in r.text
    assert r.text == "<html>spa</html>"


async def test_no_static_dir_means_no_spa(tmp_path: Path):
    async with client(create_app(static_dir=tmp_path)) as c:
        assert (await c.get("/")).status_code == 404
