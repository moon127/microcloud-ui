"""The release workflow and the versions its builds carry (D67, D68)."""

import importlib.util
import subprocess
from pathlib import Path

import pytest
import yaml

import mcui

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("build_version", ROOT / "scripts/build_version.py")
assert _spec and _spec.loader
build_version = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_version)


def workflow(name: str) -> dict:
    data = yaml.safe_load((ROOT / ".github/workflows" / name).read_text())
    data["on"] = data.pop(True, data.get("on"))  # YAML 1.1 reads the key `on` as True
    return data


def test_package_version_reads_the_package():
    assert build_version.package_version() == mcui.__version__


def test_package_version_needs_a_version(tmp_path):
    init = tmp_path / "__init__.py"
    init.write_text('"""no version"""\n')
    with pytest.raises(ValueError, match="no __version__"):
        build_version.package_version(init)


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
    result = build_version.check(tag, version)
    assert result is None if error is None else error in result


@pytest.mark.parametrize(
    ("version", "commits", "expected"),
    [
        ("0.1.1.dev0", 7, "0.1.1.dev7"),
        ("0.2.0.dev3", 0, "0.2.0.dev0"),
        ("0.1.0", 0, "0.1.0"),  # the release commit itself
        ("0.1.0", 2, "0.1.0"),
    ],
)
def test_dev_version(version, commits, expected):
    assert build_version.dev_version(version, commits) == expected


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.org", *args],  # noqa: S607
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def test_commits_since_release(tmp_path):
    _git(tmp_path, "init", "-q")
    for n in range(3):
        _git(tmp_path, "commit", "-q", "--allow-empty", "-m", f"c{n}")
    assert build_version.commits_since_release(tmp_path) == 3  # no release yet
    _git(tmp_path, "tag", "-a", "v0.1.0", "-m", "r")
    _git(tmp_path, "tag", "edge")  # other tags don't count
    assert build_version.commits_since_release(tmp_path) == 0
    for n in range(2):
        _git(tmp_path, "commit", "-q", "--allow-empty", "-m", f"d{n}")
    assert build_version.commits_since_release(tmp_path) == 2


def test_changes(tmp_path):
    _git(tmp_path, "init", "-q")
    for subject in ("First feature", "Release 0.1.0"):
        _git(tmp_path, "commit", "-q", "--allow-empty", "-m", subject)
    _git(tmp_path, "tag", "-a", "v0.1.0", "-m", "r")
    for subject in ("Back to development: 0.1.1.dev0", "Fix `tables` & $HOME", "Release 0.1.1"):
        _git(tmp_path, "commit", "-q", "--allow-empty", "-m", subject)
    items = build_version.changes("v0.1.0", tmp_path)
    assert len(items) == 1 and items[0].startswith("- Fix `tables` & $HOME (")  # bumps left out
    assert [i.split(" (")[0] for i in build_version.changes(None, tmp_path)] == [
        "- Fix `tables` & $HOME",
        "- First feature",
    ]
    assert build_version.changes("HEAD", tmp_path) == []
    # "Release" alone at the start of a real change isn't a version bump
    assert build_version.HOUSEKEEPING.match("Release workflow: tags") is None


def test_main(monkeypatch, capsys):
    monkeypatch.setattr(build_version, "package_version", lambda: "0.2.0")
    assert build_version.main(["build_version.py", "release", "v0.2.0"]) == 0
    assert capsys.readouterr().out == "0.2.0\n"
    assert build_version.main(["build_version.py", "release", "v0.2.1"]) == 1
    assert "does not match" in capsys.readouterr().err
    monkeypatch.setattr(build_version, "package_version", lambda: "0.2.1.dev0")
    monkeypatch.setattr(build_version, "commits_since_release", lambda: 4)
    assert build_version.main(["build_version.py", "dev"]) == 0
    assert capsys.readouterr().out == "0.2.1.dev4\n"
    monkeypatch.setattr(build_version, "changes", lambda since: [f"- since {since} (abc1234)"])
    assert build_version.main(["build_version.py", "changes", "v0.2.0"]) == 0
    assert capsys.readouterr().out == "- since v0.2.0 (abc1234)\n"
    monkeypatch.setattr(build_version, "changes", lambda since: [])
    assert build_version.main(["build_version.py", "changes"]) == 0
    assert capsys.readouterr().out == "- (no changes)\n"
    assert build_version.main(["build_version.py"]) == 2
    assert build_version.main(["build_version.py", "release"]) == 2


def test_triggers_and_modes():
    release, ci = workflow("release.yml"), workflow("ci.yml")
    assert "workflow_call" in ci["on"] and release["on"]["push"]["tags"] == ["v*"]
    assert release["on"]["workflow_run"] == {
        "workflows": ["CI"],
        "types": ["completed"],
        "branches": ["main"],
    }
    assert "workflow_dispatch" in release["on"]
    jobs = release["jobs"]
    # edge: only CI runs that passed on a push to this repository (never a fork's PR)
    gate = jobs["setup"]["if"]
    for cond in (
        "github.event.workflow_run.conclusion == 'success'",
        "github.event.workflow_run.event == 'push'",
        "github.event.workflow_run.head_repository.full_name == github.repository",
    ):
        assert cond in gate
    mode = jobs["setup"]["steps"][-1]["run"]
    assert 'build_version.py release "$GITHUB_REF_NAME"' in mode
    assert "build_version.py dev" in mode
    assert 'tags="$IMAGE:$version $IMAGE:${version%.*} $IMAGE:latest"' in mode
    assert 'tags="$IMAGE:edge $IMAGE:edge-${REF::7}"' in mode
    # notes list the commits since the previous release (a release) or the latest (edge)
    assert 'build_version.py changes ${since:+"$since"}' in mode
    assert 'echo "changes<<$delim"' in mode
    # CI runs inline except for edge, which follows a CI run that passed
    assert jobs["ci"]["uses"] == "./.github/workflows/ci.yml"
    assert jobs["ci"]["if"] == "needs.setup.outputs.mode != 'edge'"
    assert release["concurrency"]["cancel-in-progress"] == (
        "${{ github.event_name == 'workflow_run' }}"
    )


def test_builds():
    jobs = workflow("release.yml")["jobs"]
    # each platform on its own native runner, no emulation
    matrix = {m["platform"]: m["runner"] for m in jobs["image"]["strategy"]["matrix"]["include"]}
    assert matrix == {"linux/amd64": "ubuntu-24.04", "linux/arm64": "ubuntu-24.04-arm"}
    for job in ("image", "bundle"):
        steps = jobs[job]["steps"]
        assert steps[0]["with"]["ref"] == "${{ needs.setup.outputs.ref }}"
        assert steps[1]["name"] == "Stamp the version" and "__version__" in steps[1]["run"]
    steps = {s.get("uses", "").split("@")[0]: s for s in jobs["image"]["steps"]}
    assert "docker/setup-qemu-action" not in steps
    build = steps["docker/build-push-action"]["with"]
    assert build["file"] == "deploy/container/Containerfile"
    assert build["platforms"] == "${{ matrix.platform }}"
    # a dry run builds both and publishes nothing
    assert jobs["image"]["env"]["PUBLISH"] == "${{ needs.setup.outputs.mode != 'dry-run' }}"
    assert build["outputs"].endswith(
        "push-by-digest=true,name-canonical=true,push=${{ env.PUBLISH }}"
    )
    assert "needs.setup.outputs.mode != 'dry-run'" in jobs["merge"]["if"]
    assert "imagetools create" in jobs["merge"]["steps"][-1]["run"]
    assert "org.opencontainers.image.version=" in build["labels"]


def test_publishing():
    release = workflow("release.yml")
    jobs = release["jobs"]
    steps = {s.get("name"): s for s in jobs["release"]["steps"]}
    tagged = steps["GitHub Release for the tag"]
    assert tagged["if"] == "env.MODE == 'release'" and "--notes-start-tag" in tagged["run"]
    edge = steps['Rolling pre-release "edge"']
    assert edge["if"] == "env.MODE == 'edge'"
    assert jobs["release"]["env"]["CHANGES"] == "${{ needs.setup.outputs.changes }}"
    for step in (tagged, edge):
        assert "## Changes${SINCE:+ since $SINCE}" in step["run"] and "$CHANGES" in step["run"]
    for part in ("refs/tags/edge", "--prerelease --latest=false", "delete-asset edge"):
        assert part in edge["run"]
    # the only write permissions: packages for the image, contents for the release
    assert release["permissions"] == {"contents": "read"}
    assert jobs["image"]["permissions"]["packages"] == "write"
    assert jobs["merge"]["permissions"]["packages"] == "write"
    assert jobs["release"]["permissions"] == {"contents": "write"}


def test_ubuntu_lts_releases():
    """Bundles for each supported LTS's Python; the image on the newest LTS (D72)."""
    ci, jobs = workflow("ci.yml"), workflow("release.yml")["jobs"]
    assert ci["jobs"]["backend"]["strategy"]["matrix"]["python"] == ["3.10", "3.12", "3.14"]
    loop = [s["run"] for s in jobs["bundle"]["steps"] if "make bundle" in s.get("run", "")]
    assert loop and "for pyver in 3.14 3.12 3.10; do" in loop[0]
    image = (ROOT / "deploy/container/Containerfile").read_text()
    assert image.count("FROM docker.io/library/ubuntu:26.04") == 2


def test_spa_stage_builds_natively():
    """A local cross-build (e.g. arm64 under emulation) is slow; the web UI is plain JS."""
    image = (ROOT / "deploy/container/Containerfile").read_text()
    assert "FROM --platform=$BUILDPLATFORM docker.io/library/node:22-bookworm-slim AS spa" in image
