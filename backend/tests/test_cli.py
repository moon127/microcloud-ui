import pytest

from mcui import __version__, cli


def test_version(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_command_required():
    with pytest.raises(SystemExit) as e:
        cli.main([])
    assert e.value.code == 2


def test_serve_invokes_uvicorn(monkeypatch):
    calls = {}

    def fake_run(app, **kw):
        calls["app"] = app
        calls.update(kw)

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", fake_run)
    assert cli.main(["serve", "--port", "9001", "--reload"]) == 0
    assert calls == {"app": "mcui.main:app", "host": "127.0.0.1", "port": 9001, "reload": True}


def test_module_entrypoint(monkeypatch):
    import runpy

    monkeypatch.setattr("sys.argv", ["mcui", "--version"])
    with pytest.raises(SystemExit) as e:
        runpy.run_module("mcui", run_name="__main__")
    assert e.value.code == 0
