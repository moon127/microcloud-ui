"""The folder browser helpers' image check (D97): the script and its weekly job."""

import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from tests.test_release import workflow

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("helper_image", ROOT / "scripts/helper_image.py")
assert _spec and _spec.loader
helper_image = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(helper_image)

ROOTFS = b"squashfs bytes"


def product(alias, serials, arch="amd64"):
    return {
        "aliases": f"{alias}/default,{alias}",
        "arch": arch,
        "versions": {
            s: {
                "items": {
                    "rootfs.squashfs": {
                        "path": f"images/{alias}/{s}/rootfs.squashfs",
                        "sha256": hashlib.sha256(ROOTFS).hexdigest(),
                        "size": len(ROOTFS),
                    }
                }
            }
            for s in serials
        },
    }


# the shape of images.lxd.canonical.com's streams/v1/images.json, trimmed
INDEX = {
    "products": {
        "alpine:3.24:amd64:default": product("alpine/3.24", ["20261126_0052", "20261127_0052"]),
        "alpine:3.24:arm64:default": product("alpine/3.24", ["20261128_0052"], "arm64"),
        "alpine:3.9:amd64:default": product("alpine/3.9", ["20200101_0000"]),
        "alpine:edge:amd64:default": product("alpine/edge", ["20261127_0052"]),
    }
}
# alpinelinux.org/releases.json, trimmed
RELEASES = {
    "release_branches": [{"rel_branch": "edge"}, {"rel_branch": "v3.24", "eol_date": "2028-06-01"}]
}


def test_the_image_and_its_release():
    config = (ROOT / "backend/mcui/config.py").read_text()
    assert helper_image.default_image(config) == "alpine/3.24"
    with pytest.raises(SystemExit):
        helper_image.default_image("nothing")
    build = helper_image.latest(INDEX, "alpine/3.24")
    assert build["serial"] == "20261127_0052" and build["product"] == "alpine:3.24:amd64:default"
    with pytest.raises(SystemExit, match="isn't on"):
        helper_image.latest(INDEX, "alpine/9.9")
    assert helper_image.newest_stable(INDEX) == "alpine/3.24"  # 3.24 > 3.9, edge isn't one
    assert helper_image.newest_stable({"products": {}}) is None
    assert helper_image.eol(RELEASES, "alpine/3.24") == "2028-06-01"
    assert helper_image.eol(RELEASES, "alpine/edge") is None
    assert helper_image.eol(RELEASES, "alpine/3.1") is None


def test_fetch(tmp_path, monkeypatch):
    pages = {
        f"{helper_image.SERVER}/streams/v1/images.json": json.dumps(INDEX).encode(),
        f"{helper_image.SERVER}/images/alpine/3.24/20261127_0052/rootfs.squashfs": ROOTFS,
        helper_image.RELEASES: json.dumps(RELEASES).encode(),
    }
    monkeypatch.setattr(helper_image, "_get", lambda url: pages[url])
    info = helper_image.fetch(tmp_path / "out")
    assert info == {
        "alias": "alpine/3.24",
        "serial": "20261127_0052",
        "newest": "alpine/3.24",
        "eol": "2028-06-01",
    }
    assert (tmp_path / "out/rootfs.squashfs").read_bytes() == ROOTFS
    assert json.loads((tmp_path / "out/image.json").read_text()) == info

    # Alpine's page away: still checked, without the date
    def away(url):
        if url == helper_image.RELEASES:
            raise OSError("down")
        return pages[url]

    monkeypatch.setattr(helper_image, "_get", away)
    assert helper_image.fetch(tmp_path / "out")["eol"] is None
    # a download that doesn't match the index is refused
    pages[f"{helper_image.SERVER}/images/alpine/3.24/20261127_0052/rootfs.squashfs"] = b"tampered"
    monkeypatch.setattr(helper_image, "_get", lambda url: pages[url])
    with pytest.raises(SystemExit, match="sha256"):
        helper_image.fetch(tmp_path / "out")


TRIVY = {
    "Metadata": {"OS": {"Family": "alpine", "Name": "3.24.1"}},
    "Results": [
        {
            "Target": "helper/rootfs (alpine 3.24.1)",
            "Class": "os-pkgs",
            "Packages": [{"Name": "busybox"}, {"Name": "musl"}],
            "Vulnerabilities": [
                {
                    "VulnerabilityID": "CVE-2026-1",
                    "PkgName": "busybox",
                    "InstalledVersion": "1.37.0-r20",
                    "FixedVersion": "1.37.0-r21",
                    "Severity": "HIGH",
                },
                {
                    "VulnerabilityID": "CVE-2026-2",
                    "PkgName": "musl",
                    "InstalledVersion": "1.2.5-r10",
                    "FixedVersion": "1.2.5-r11",
                    "Severity": "CRITICAL",
                },
                {
                    "VulnerabilityID": "CVE-2026-3",
                    "PkgName": "zlib",
                    "InstalledVersion": "1.3",
                    "Severity": "HIGH",
                },
            ],
        }
    ],
}
INFO = {
    "alias": "alpine/3.24",
    "serial": "20261127_0052",
    "newest": "alpine/3.24",
    "eol": "2028-06-01",
}
TODAY = dt.date(2026, 11, 28)


def test_report():
    assert helper_image.body({"Results": []}, INFO, TODAY) == ""  # all's well: no issue
    helper_image.check_scanned(TRIVY)
    # an empty scan isn't a clean one: the run fails rather than close a real issue
    for empty in [
        {"Results": []},
        {**TRIVY, "Metadata": {"OS": {"Family": "debian"}}},
        {**TRIVY, "Results": [{**TRIVY["Results"][0], "Packages": []}]},
        {**TRIVY, "Results": [{**TRIVY["Results"][0], "Class": "lang-pkgs"}]},
    ]:
        with pytest.raises(helper_image.NoPackages):
            helper_image.check_scanned(empty)
    text = helper_image.body(TRIVY, INFO, TODAY)
    lines = text.splitlines()
    assert "2 fixable HIGH or CRITICAL" in lines[0] and "build 20261127_0052" in lines[0]
    assert lines[4].startswith("| `CVE-2026-2` | `CRITICAL` | `musl`")  # most severe first
    assert "CVE-2026-3" not in text  # no fix yet
    newer = helper_image.body({}, {**INFO, "newest": "alpine/3.25"}, TODAY)
    assert "newer Alpine release is out: `alpine/3.25`" in newer
    ending = helper_image.body({}, {**INFO, "eol": "2027-01-15"}, TODAY)
    assert "end of Alpine's support on 2027-01-15" in ending
    edge = {**INFO, "alias": "alpine/edge", "newest": "alpine/3.24", "eol": None}
    assert helper_image.body({}, edge, TODAY) == ""
    # the image server's builds stalled, or a serial that isn't one
    stalled = helper_image.body({}, {**INFO, "serial": "20261101_0052"}, TODAY)
    assert "normally builds daily" in stalled
    assert "is unknown" in helper_image.body({}, {**INFO, "serial": "x\n# hi"}, TODAY)
    # text from the image or Trivy's database can't break the table or mention anyone
    evil = {"VulnerabilityID": "CVE-1", "PkgName": "x|y\n# hi @someone", "FixedVersion": "`2`"}
    text = helper_image.body({"Results": [{"Vulnerabilities": [evil]}]}, INFO, TODAY)
    row = next(line for line in text.splitlines() if "CVE-1" in line)
    assert row == "| `CVE-1` |  | `x y # hi @someone` |  | `2` |"


def test_main(tmp_path, capsys):
    (tmp_path / "t.json").write_text(json.dumps(TRIVY))
    (tmp_path / "i.json").write_text(json.dumps(INFO))
    assert (
        helper_image.main(["x", "report", str(tmp_path / "t.json"), str(tmp_path / "i.json")]) == 0
    )
    assert "CVE-2026-1" in capsys.readouterr().out
    assert helper_image.main(["x"]) == 2
    (tmp_path / "t.json").write_text(json.dumps({"Results": []}))
    with pytest.raises(SystemExit, match="no Alpine packages"):
        helper_image.main(["x", "report", str(tmp_path / "t.json"), str(tmp_path / "i.json")])


def test_weekly_job():
    job = workflow("security.yml")["jobs"]["helper-image"]
    assert job["permissions"] == {"contents": "read", "issues": "write"}
    runs = "\n".join(s.get("run", "") for s in job["steps"])
    assert "helper_image.py fetch helper" in runs and "unsquashfs" in runs
    assert "sudo unsquashfs" not in runs and "apt-get -qq update" in runs
    assert "test -e helper/rootfs/etc/alpine-release" in runs
    checkout = job["steps"][0]
    assert checkout["with"]["persist-credentials"] is False  # no token beside the download
    scan = next(s for s in job["steps"] if "trivy-action" in s.get("uses", ""))
    assert scan["with"]["scan-type"] == "rootfs" and scan["with"]["ignore-unfixed"] is True
    assert scan["with"]["list-all-pkgs"] is True
    assert "@" in scan["uses"] and len(scan["uses"].split("@")[1].split()[0]) == 40  # pinned
    assert "--label helper-image" in runs
