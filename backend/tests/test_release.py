"""The release workflow and its tag check (D67)."""

import importlib.util
from pathlib import Path

import pytest
import yaml

import mcui

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("release_check", ROOT / "scripts/release_check.py")
assert _spec and _spec.loader
release_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_check)


def workflow(name: str) -> dict:
    data = yaml.safe_load((ROOT / ".github/workflows" / name).read_text())
    data["on"] = data.pop(True, data.get("on"))  # YAML 1.1 reads the key `on` as True
    return data


def test_package_version_reads_the_package():
    assert release_check.package_version() == mcui.__version__


def test_package_version_needs_a_version(tmp_path):
    init = tmp_path / "__init__.py"
    init.write_text('"""no version"""\n')
    with pytest.raises(ValueError, match="no __version__"):
        release_check.package_version(init)


@pytest.mark.parametrize(
    ("tag", "version", "error"),
    [
        ("v0.1.0", "0.1.0", None),
        ("v1.12.3", "1.12.3", None),
        ("v0.1.0", "0.1.0.dev0", "set it to X.Y.Z"),
        ("v0.1.0rc1", "0.1.0rc1", "set it to X.Y.Z"),
        ("v0.1.1", "0.1.0", "does not match"),
        ("0.1.0", "0.1.0", "expected 'v0.1.0'"),
    ],
)
def test_check(tag, version, error):
    result = release_check.check(tag, version)
    assert result is None if error is None else error in result


def test_main(monkeypatch, capsys):
    monkeypatch.setattr(release_check, "package_version", lambda: "0.2.0")
    assert release_check.main(["release_check.py", "v0.2.0"]) == 0
    assert "v0.2.0 ok" in capsys.readouterr().out
    assert release_check.main(["release_check.py", "v0.2.1"]) == 1
    assert "does not match" in capsys.readouterr().err
    assert release_check.main(["release_check.py"]) == 2


def test_release_workflow():
    release, ci = workflow("release.yml"), workflow("ci.yml")
    assert "workflow_call" in ci["on"] and release["on"]["push"]["tags"] == ["v*"]
    jobs = release["jobs"]
    assert jobs["ci"]["uses"] == "./.github/workflows/ci.yml"
    assert set(jobs["image"]["needs"]) == set(jobs["bundle"]["needs"]) == {"ci", "version"}
    steps = {s.get("uses", "").split("@")[0]: s for s in jobs["image"]["steps"]}
    build = steps["docker/build-push-action"]["with"]
    assert build["file"] == "deploy/container/Containerfile"
    assert build["platforms"] == "linux/amd64,linux/arm64"
    # a hand-run (workflow_dispatch) builds everything and publishes nothing
    assert build["push"] == "${{ env.PUBLISH == 'true' }}"
    assert release["env"]["PUBLISH"] == "${{ startsWith(github.ref, 'refs/tags/v') }}"
    assert jobs["release"]["if"] == "startsWith(github.ref, 'refs/tags/v')"
    assert steps["docker/metadata-action"]["with"]["images"] == "ghcr.io/${{ github.repository }}"
    check = [s for s in jobs["version"]["steps"] if "release_check.py" in s.get("run", "")]
    assert check and check[0]["if"] == "env.PUBLISH == 'true'"
    # the only write permissions: packages for the image, contents for the release
    assert release["permissions"] == {"contents": "read"}
    assert jobs["image"]["permissions"]["packages"] == "write"
    assert jobs["release"]["permissions"] == {"contents": "write"}


def test_spa_stage_builds_natively():
    """Cross-building arm64 under emulation is slow; the web UI is plain JS."""
    image = (ROOT / "deploy/container/Containerfile").read_text()
    assert "FROM --platform=$BUILDPLATFORM docker.io/library/node:22-bookworm-slim AS spa" in image
