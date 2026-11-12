"""Check a release tag against mcui.__version__ (D67). Run by .github/workflows/release.yml.

    python scripts/release_check.py v0.1.0

Releases are final versions X.Y.Z only, and the tag is exactly "v" + the version, so
the image tags, the bundle names and the program all say the same thing.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

INIT = Path(__file__).resolve().parents[1] / "backend" / "mcui" / "__init__.py"


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


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: release_check.py <tag>", file=sys.stderr)
        return 2
    error = check(argv[1], package_version())
    if error:
        print(f"release_check: {error}", file=sys.stderr)
        return 1
    print(f"release_check: {argv[1]} ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
