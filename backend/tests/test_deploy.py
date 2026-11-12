"""The production files in deploy/ agree with the code and with each other (D63)."""

import re
import sys
from pathlib import Path

from mcui.config import Settings

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"


def read(name: str) -> str:
    return (DEPLOY / name).read_text()


def test_example_config_is_valid(tmp_path):
    data = tomllib.loads(read("config.example.toml"))
    server = data["server"]
    assert server["data_dir"] == "/var/lib/mcui" and server["port"] == 8843
    assert server["secure_cookies"] is True
    # the TLS files only exist on the host; everything else must validate as written
    cert, key = tmp_path / "ui.crt", tmp_path / "ui.key"
    cert.write_text("x")
    key.write_text("x")
    key.chmod(0o600)
    Settings(**{**data, "server": {**server, "tls_cert": cert, "tls_key": key}})


def test_commented_sections_use_real_keys():
    """Uncommenting the [lxd]/[metrics]/[node_exporter] examples gives known keys only."""
    lines = [
        line[2:]
        for line in read("config.example.toml").splitlines()
        if re.match(r"# (\[\w+\]|\w+ *=)", line)
    ]
    data = tomllib.loads("\n".join(lines))
    assert set(data) == {"lxd", "metrics", "node_exporter"}
    for section, keys in data.items():
        model = Settings.model_fields[section].annotation
        if not hasattr(model, "model_fields"):  # "X | None": the model in the union
            (model,) = [a for a in model.__args__ if a is not type(None)]
        allowed = set(model.model_fields)
        assert set(keys) <= allowed, (section, set(keys) - allowed)


def test_paths_agree():
    unit, cli, install = read("mcui.service"), read("mcui-cli"), read("install.sh")
    run = "/opt/mcui/current/bin/mcui -c /etc/mcui/config.toml"
    assert f"ExecStart={run} serve" in unit
    assert f"runuser -u mcui -- {run}" in cli
    assert "PREFIX=/opt/mcui" in install and "ETC=/etc/mcui" in install
    assert "DATA=/var/lib/mcui" in install
    assert "ReadWritePaths=/var/lib/mcui" in unit and "User=mcui" in unit
    assert 'tls_cert = "/etc/mcui/certs/ui.crt"' in read("config.example.toml")
    assert '"$ETC/certs/ui.crt"' in install and '"$ETC/certs/ui.key"' in install
