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
    """Uncommenting the [server] proxy keys and the [lxd]/[metrics]/[node_exporter] examples
    gives known keys only."""
    lines = [
        line[2:]
        for line in read("config.example.toml").splitlines()
        if re.match(r"# (\[\w+\]|\w+ *=)", line)
    ]
    data = tomllib.loads("[server]\n" + "\n".join(lines))  # the first ones are [server]'s
    assert set(data) == {"server", "lxd", "metrics", "node_exporter"}
    assert set(data["server"]) == {"public_url", "forwarded_allow_ips"}
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
    assert "groupadd --system --gid 8843 mcui" in install  # same ids as the image
    assert "ReadWritePaths=/var/lib/mcui" in unit and "User=mcui" in unit
    assert 'tls_cert = "/var/lib/mcui/tls/ui.crt"' in read("config.example.toml")
    assert (
        'tls="$DATA/tls"' in install and '"$tls/ui.crt"' in install and '"$tls/ui.key"' in install
    )


def test_container_files_agree():
    image = read("container/Containerfile")
    quadlet, compose = read("container/mcui.container"), read("container/compose.yaml")
    entry = read("container/entrypoint.sh")
    # one config for both: the image ships the example as /etc/mcui/config.toml
    assert "COPY --chmod=0640 deploy/config.example.toml /etc/mcui/config.toml" in image
    assert "MCUI_CONFIG=/etc/mcui/config.toml" in image
    assert "--uid 8843" in image and "USER mcui" in image and "VOLUME /var/lib/mcui" in image
    assert "EXPOSE 8843" in image and 'CMD ["serve"]' in image
    # the entrypoint makes the self-signed pair where the example config looks for it
    assert "tls=/var/lib/mcui/tls" in entry
    for f in (quadlet, compose):
        assert "/etc/mcui:ro" in f and "mcui-data:/var/lib/mcui" in f
    for setting in ("Network=host", "ReadOnly=true", "DropCapability=ALL", "NoNewPrivileges=true"):
        assert setting in quadlet
    assert "RestartPreventExitStatus=2" in quadlet
    assert "read_only: true" in compose and "- ALL" in compose and "network: host" in compose
    # everything the image build copies is in the build context
    ignore = (DEPLOY.parent / ".dockerignore").read_text()
    for path in ("deploy/config.example.toml", "deploy/container/entrypoint.sh", "LICENSE"):
        assert f"!{path}" in ignore


def test_bundle_names_the_backports():
    """pip download judges markers by the running Python, so bundle.sh names each backport
    the oldest supported target (3.10) needs (D67)."""
    project = tomllib.loads((DEPLOY.parent / "backend/pyproject.toml").read_text())["project"]
    backports = [d.split(";")[0] for d in project["dependencies"] if "python_version <" in d]
    assert backports
    bundle = (DEPLOY.parent / "scripts/bundle.sh").read_text()
    for dep in backports:
        assert f'3.10) extra+=("{dep}")' in bundle
