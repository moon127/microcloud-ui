from pathlib import Path

import pytest

from mcui.config import ConfigError, Settings, check_key_file, load_settings
from tests.conftest import EP1, SERVER_NAME


def write_config(tmp_path: Path, certs, extra: str = "") -> Path:
    p = tmp_path / "config.toml"
    p.write_text(
        f"""
[server]
data_dir = "{tmp_path}/data"

[lxd]
endpoints = ["{EP1}/"]
server_cert = "{certs.server_cert}"
server_name = "{SERVER_NAME}"
client_cert = "{certs.client_cert}"
client_key = "{certs.client_key}"
{extra}
"""
    )
    return p


def test_defaults_without_file(monkeypatch):
    monkeypatch.delenv("MCUI_CONFIG", raising=False)
    s = load_settings()
    assert s.lxd is None
    assert s.metrics.port == 8443
    assert s.metrics.interval_seconds == 15
    assert s.server.secure_cookies is True
    assert s.metrics_cert is None


def test_load_toml(tmp_path, certs):
    s = load_settings(write_config(tmp_path, certs))
    assert s.lxd is not None
    assert s.lxd.endpoints == [EP1]  # trailing slash stripped
    assert s.lxd.server_name == SERVER_NAME
    assert s.server.data_dir == tmp_path / "data"
    # metrics falls back to the management cert (D5)
    assert s.metrics_cert == (certs.client_cert, certs.client_key)


def test_env_overrides_file(tmp_path, certs, monkeypatch):
    p = write_config(tmp_path, certs, "[metrics]\nport = 8443\n")
    monkeypatch.setenv("MCUI_CONFIG", str(p))
    monkeypatch.setenv("MCUI_METRICS__PORT", "8444")
    monkeypatch.setenv("MCUI_LXD__SERVER_NAME", "other")
    s = load_settings()
    assert s.metrics.port == 8444
    assert s.lxd.server_name == "other"
    assert s.lxd.endpoints == [EP1]


def test_metrics_cert_pair(tmp_path, certs):
    extra = f'[metrics]\ncert = "{certs.other_cert}"\nkey = "{certs.other_key}"\n'
    s = load_settings(write_config(tmp_path, certs, extra))
    assert s.metrics_cert == (certs.other_cert, certs.other_key)


def test_metrics_cert_without_key(tmp_path, certs):
    extra = f'[metrics]\ncert = "{certs.other_cert}"\n'
    with pytest.raises(ConfigError, match="together"):
        load_settings(write_config(tmp_path, certs, extra))


def test_server_tls(tmp_path, certs):
    s = Settings(server={"tls_cert": str(certs.other_cert), "tls_key": str(certs.other_key)})
    assert s.server.tls_cert == certs.other_cert and s.server.port == 8000
    with pytest.raises(ValueError, match="together"):
        Settings(server={"tls_cert": str(certs.other_cert)})
    with pytest.raises(ValueError, match="file not found"):
        Settings(server={"tls_cert": str(tmp_path / "nope"), "tls_key": str(certs.other_key)})
    loose = tmp_path / "loose.key"  # a copy: the certs fixture is shared
    loose.write_bytes(certs.other_key.read_bytes())
    loose.chmod(0o640)
    with pytest.raises(ValueError, match="chmod 600"):
        Settings(server={"tls_cert": str(certs.other_cert), "tls_key": str(loose)})


def test_server_proxy_settings():
    s = Settings(
        server={"public_url": "HTTPS://Mcui.Example.org:8443/", "forwarded_allow_ips": "*"}
    )
    assert s.server.public_url == "https://mcui.example.org:8443"
    assert s.server.forwarded_allow_ips == "*"
    assert Settings().server.public_url is None
    for bad in ("mcui.example.org", "ftp://x", "https://", "https://x/ui"):
        with pytest.raises(ValueError, match="public_url"):
            Settings(server={"public_url": bad})


def test_interval_minimum(tmp_path, certs):
    with pytest.raises(ConfigError, match="interval_seconds"):
        load_settings(write_config(tmp_path, certs, "[metrics]\ninterval_seconds = 5\n"))


def test_https_only(certs):
    with pytest.raises(ValueError, match="https"):
        Settings(
            lxd={
                "endpoints": ["http://x:8443"],
                "server_cert": certs.server_cert,
                "client_cert": certs.client_cert,
                "client_key": certs.client_key,
            }
        )


def test_unknown_key_rejected(tmp_path, certs):
    with pytest.raises(ConfigError, match="nope"):
        load_settings(write_config(tmp_path, certs, "nope = 1\n"))


def test_missing_cert_file(tmp_path, certs):
    p = write_config(tmp_path, certs).read_text().replace(str(certs.server_cert), "/no/such.crt")
    (tmp_path / "c.toml").write_text(p)
    with pytest.raises(ConfigError, match="file not found"):
        load_settings(tmp_path / "c.toml")


def test_world_readable_key_refused(tmp_path, certs):
    key = tmp_path / "loose.key"
    key.write_bytes(certs.client_key.read_bytes())
    key.chmod(0o644)
    with pytest.raises(ValueError, match="chmod 600"):
        check_key_file(key)
    key.chmod(0o600)
    check_key_file(key)


def test_missing_key(tmp_path):
    with pytest.raises(ValueError, match="cannot read key file"):
        check_key_file(tmp_path / "nope.key")


def test_tilde_expanded(certs, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(certs.dir))
    s = Settings(
        server={"data_dir": "~/data"},
        lxd={
            "endpoints": [EP1],
            "server_cert": "~/server.crt",
            "client_cert": "~/client.crt",
            "client_key": "~/client.key",
        },
    )
    assert s.lxd.client_key == certs.client_key
    assert s.server.data_dir == certs.dir / "data"


def test_unreadable_config(tmp_path):
    with pytest.raises(ConfigError, match="cannot read config"):
        load_settings(tmp_path / "missing.toml")


def test_bad_toml(tmp_path):
    p = tmp_path / "bad.toml"
    p.write_text("[lxd\n")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_settings(p)
