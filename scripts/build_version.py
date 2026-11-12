"""The version a build carries (D67, D68). Run by .github/workflows/release.yml.

    python scripts/build_version.py release v0.1.0   # checks the tag, prints 0.1.0
    python scripts/build_version.py dev              # prints 0.1.1.dev<N> for HEAD

A release is a final X.Y.Z, and its tag is exactly "v" + mcui.__version__, so the image
tags, the bundle names and the program all say the same thing. A dev build (the edge
channel) counts the commits since the last release tag, so every build of main has its
own version: "0.1.1.dev0" in the source becomes 0.1.1.dev7 seven commits on.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "backend" / "mcui" / "__init__.py"


def package_version(init: Path = INIT) -> str:
    match = re.search(r'^__version__ = "([^"]+)"', init.read_text(), re.MULTILINE)
    if not match:
        raise ValueError(f"no __version__ in {init}")
    return match.group(1)


def check(tag: str, version: str) -> str | None:
    """None when the tag may be released, otherwise why not."""
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        return f"mcui.__version__ is {version!r}; set it to X.Y.Z before tagging a release"
    if tag != f"v{version}":
        return f"tag {tag!r} does not match mcui.__version__ {version!r} (expected 'v{version}')"
    return None


def dev_version(version: str, commits: int) -> str:
    """X.Y.Z.devN with N = commits since the last release; a final version is kept."""
    base, dev = re.subn(r"\.dev\d+$", "", version)
    return f"{base}.dev{commits}" if dev else version


def commits_since_release(cwd: Path = ROOT) -> int:
    def git(*args: str) -> str:
        return subprocess.run(  # noqa: S603
            ["git", *args],  # noqa: S607
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    try:
        tag = git("describe", "--tags", "--abbrev=0", "--match", "v[0-9]*")
    except subprocess.CalledProcessError:  # no release yet
        return int(git("rev-list", "--count", "HEAD"))
    return int(git("rev-list", "--count", f"{tag}..HEAD"))


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[1] == "release":
        version = package_version()
        error = check(argv[2], version)
        if error:
            print(f"build_version: {error}", file=sys.stderr)
            return 1
        print(version)
        return 0
    if len(argv) == 2 and argv[1] == "dev":
        print(dev_version(package_version(), commits_since_release()))
        return 0
    print("usage: build_version.py release <tag> | dev", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
