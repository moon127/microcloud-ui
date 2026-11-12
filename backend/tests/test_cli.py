import io
import os

import httpx
import pytest
import respx

import mcui.config
from mcui import __version__, cli
from mcui.auth.store import Store
from tests.conftest import EP1, fixture_json, fixture_text, lxd_ok


def test_version(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_command_required():
    with pytest.raises(SystemExit) as e:
        cli.main([])
    assert e.value.code == 2


@pytest.fixture
def config(tmp_path, certs, monkeypatch):
    monkeypatch.delenv("MCUI_CONFIG", raising=False)
    p = tmp_path / "config.toml"
    p.write_text(
        f"""[server]
data_dir = "{tmp_path}/data"
[lxd]
endpoints = ["{EP1}"]
server_cert = "{certs.server_cert}"
server_name = "test-cluster"
client_cert = "{certs.client_cert}"
client_key = "{certs.client_key}"
"""
    )
    return p


@pytest.fixture
def no_lxd_config(tmp_path, monkeypatch):
    monkeypatch.delenv("MCUI_CONFIG", raising=False)
    p = tmp_path / "plain.toml"
    p.write_text(f'[server]\ndata_dir = "{tmp_path}/data"\n')
    return p


@pytest.fixture
def fake_uvicorn(monkeypatch):
    calls = {}

    def fake_run(app, **kw):
        calls["app"] = app
        calls.update(kw)

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", fake_run)
    return calls


def test_serve_invokes_uvicorn_factory(fake_uvicorn, monkeypatch):
    monkeypatch.delenv("MCUI_CONFIG", raising=False)
    assert cli.main(["serve", "--port", "9001", "--reload"]) == 0
    assert fake_uvicorn == {
        "app": "mcui.main:create_app",
        "factory": True,
        "host": "127.0.0.1",
        "port": 9001,
        "reload": True,
        "workers": 1,
        "server_header": False,
    }


def test_serve_passes_config_via_env(fake_uvicorn, config, monkeypatch, capsys):
    assert cli.main(["--config", str(config), "serve"]) == 0
    assert os.environ["MCUI_CONFIG"] == str(config)
    assert f"mcui: config {config}" in capsys.readouterr().err


def test_serve_says_which_config(fake_uvicorn, monkeypatch, capsys):
    """Unconfigured is easy to miss (no cluster, no users): say so (D71)."""
    assert cli.main(["serve"]) == 0
    assert "no config file; built-in defaults" in capsys.readouterr().err
    default = mcui.config.DEFAULT_CONFIG
    default.write_text("")
    assert cli.main(["serve"]) == 0
    assert f"mcui: config {default}" in capsys.readouterr().err


def test_serve_listen_and_tls_from_config(fake_uvicorn, tmp_path, certs, monkeypatch):
    monkeypatch.delenv("MCUI_CONFIG", raising=False)
    p = tmp_path / "prod.toml"
    p.write_text(
        f"""[server]
data_dir = "{tmp_path}/data"
host = "0.0.0.0"
port = 8843
tls_cert = "{certs.other_cert}"
tls_key = "{certs.other_key}"
"""
    )
    assert cli.main(["-c", str(p), "serve"]) == 0
    assert fake_uvicorn["host"] == "0.0.0.0" and fake_uvicorn["port"] == 8843  # noqa: S104
    assert fake_uvicorn["ssl_certfile"] == str(certs.other_cert)
    assert fake_uvicorn["ssl_keyfile"] == str(certs.other_key)
    assert "forwarded_allow_ips" not in fake_uvicorn
    p.write_text(
        p.read_text().replace("port = 8843", 'port = 8843\nforwarded_allow_ips = "192.0.2.9"')
    )
    assert cli.main(["-c", str(p), "serve"]) == 0
    assert fake_uvicorn["forwarded_allow_ips"] == "192.0.2.9"
    assert cli.main(["-c", str(p), "serve", "--host", "192.0.2.5", "--port", "9443"]) == 0
    assert fake_uvicorn["host"] == "192.0.2.5" and fake_uvicorn["port"] == 9443


def test_config_error_exit_code(tmp_path, capsys, fake_uvicorn):
    assert cli.main(["-c", str(tmp_path / "missing.toml"), "serve"]) == 2
    assert "config error: cannot read config" in capsys.readouterr().err
    assert fake_uvicorn == {}


def test_check_needs_lxd_section(no_lxd_config, capsys):
    assert cli.main(["-c", str(no_lxd_config), "check"]) == 2
    assert "no [lxd] section" in capsys.readouterr().err


@respx.mock
def test_check_ok_and_failed(config, capsys):
    respx.get(f"{EP1}/1.0").mock(return_value=lxd_ok(fixture_json("server_standalone.json")))
    route = respx.get(f"{EP1}/1.0/metrics")
    route.return_value = httpx.Response(200, text=fixture_text("metrics_m2_t0.txt"))
    respx.get("https://192.0.2.1:9100/metrics").mock(side_effect=httpx.ConnectError("refused"))
    assert cli.main(["-c", str(config), "check"]) == 0
    assert "Result: OK" in capsys.readouterr().out
    route.return_value = httpx.Response(403)
    assert cli.main(["-c", str(config), "check"]) == 1


@respx.mock
def test_record_fixtures(config, tmp_path, capsys):
    respx.get(f"{EP1}/1.0").mock(return_value=lxd_ok(fixture_json("server_standalone.json")))
    respx.get(url__regex=rf"{EP1}/1\.0/(?!metrics).+").mock(return_value=lxd_ok([]))
    route = respx.get(f"{EP1}/1.0/metrics").mock(return_value=httpx.Response(200, text="a 1\n"))
    # no Ceph mgr and no node_exporter: optional, so still a success
    respx.get(url__regex=r"https?://192\.0\.2\.1:(9283|9100)/metrics").mock(
        side_effect=httpx.ConnectError("refused")
    )
    out = tmp_path / "rec"
    assert cli.main(["-c", str(config), "record-fixtures", "--out", str(out)]) == 0
    assert (out / "metrics_solo.txt").read_text() == "a 1\n"
    route.return_value = httpx.Response(500)
    assert cli.main(["-c", str(config), "record-fixtures", "--out", str(out)]) == 1


def test_user_commands(no_lxd_config, tmp_path, capsys, monkeypatch):
    c = ["-c", str(no_lxd_config), "user"]
    monkeypatch.setattr("sys.stdin", io.StringIO("s3cret\n"))
    assert cli.main([*c, "add", "alice", "--role", "admin", "--password-stdin"]) == 0
    prompts = iter(["pw2", "pw2"])
    monkeypatch.setattr("getpass.getpass", lambda _: next(prompts))
    assert cli.main([*c, "passwd", "alice"]) == 0
    assert cli.main([*c, "list"]) == 0
    out = capsys.readouterr().out
    assert "created admin alice" in out and "password changed" in out and "alice\tadmin" in out

    store = Store.open(tmp_path / "data")
    assert store.authenticate("alice", "pw2") is not None
    store.close()

    assert cli.main([*c, "delete", "alice"]) == 0
    assert cli.main([*c, "delete", "alice"]) == 1
    assert "no such user" in capsys.readouterr().err

    store = Store.open(tmp_path / "data")
    actions = [e["action"] for e in store.audit_entries()]
    store.close()
    assert actions == ["user.delete", "user.update", "user.create"]


def test_password_mismatch(no_lxd_config, monkeypatch):
    prompts = iter(["a", "b"])
    monkeypatch.setattr("getpass.getpass", lambda _: next(prompts))
    with pytest.raises(SystemExit, match="do not match"):
        cli.main(["-c", str(no_lxd_config), "user", "add", "bob"])


def test_module_entrypoint(monkeypatch):
    import runpy

    monkeypatch.setattr("sys.argv", ["mcui", "--version"])
    with pytest.raises(SystemExit) as e:
        runpy.run_module("mcui", run_name="__main__")
    assert e.value.code == 0
