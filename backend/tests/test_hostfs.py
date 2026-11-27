"""Hidden folders and the browser switch (D87)."""

import pytest
from pydantic import ValidationError

from mcui.config import HostDevicesSettings
from mcui.hostfs import BROWSE_KEY, DEFAULT_HIDDEN, HIDDEN_KEY, effective, hidden, normalize, under


def test_paths():
    assert normalize("/srv//nfs/./a/../b/") == "/srv/nfs/b"
    assert normalize("//x") == "/x" and normalize("/") == "/"
    for bad in ("srv", "", "/a\0b"):
        with pytest.raises(ValueError):
            normalize(bad)
    assert under("/srv/nfs", ["/srv"]) and under("/srv", ["/srv/"])
    assert not under("/srvx", ["/srv"])
    assert under("/anything", ["/"])
    assert hidden("/usr/../etc/ssh", DEFAULT_HIDDEN)
    assert hidden("/dev/sda", DEFAULT_HIDDEN)
    # USB disks are offered from the inventory, so never hidden
    assert not hidden("/dev/disk/by-id/usb-Flash-0:0-part1", DEFAULT_HIDDEN)
    assert not hidden("/srv/nfs", DEFAULT_HIDDEN) and not hidden("/home", [])


def test_effective(store):
    cfg = HostDevicesSettings()
    fs = effective(cfg, store)
    assert (fs.hidden_paths, fs.hidden_source) == (list(DEFAULT_HIDDEN), "default")
    assert (fs.browse, fs.browse_source) == (False, "default")
    cfg = HostDevicesSettings(hidden_paths=["/srv/private/"], browse=True)
    fs = effective(cfg, store)
    assert (fs.hidden_paths, fs.hidden_source, fs.browse_source) == (
        ["/srv/private"],
        "config",
        "config",
    )
    store.set_setting(HIDDEN_KEY, ["/home"])
    store.set_setting(BROWSE_KEY, False)
    fs = effective(cfg, store)
    assert (fs.hidden_paths, fs.hidden_source, fs.browse, fs.browse_source) == (
        ["/home"],
        "app",
        False,
        "app",
    )
    store.set_setting(HIDDEN_KEY, None)
    assert store.get_setting(HIDDEN_KEY) is None
    store.set_setting(BROWSE_KEY, True)
    store.set_setting(BROWSE_KEY, True)  # saved again: replaced
    assert store.get_setting(BROWSE_KEY) is True


def test_config_checks():
    with pytest.raises(ValidationError, match="absolute"):
        HostDevicesSettings(hidden_paths=["home"])
    with pytest.raises(ValidationError):
        HostDevicesSettings(browse_project="../x")
    assert HostDevicesSettings().browse_project == "mcui-server"
