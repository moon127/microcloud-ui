"""The folder browser helpers' image (D97), for the weekly security check.

    python scripts/helper_image.py fetch OUT_DIR   # download the image the helpers use
    python scripts/helper_image.py report trivy.json OUT_DIR/image.json

``fetch`` reads ``browse_image``'s default from backend/mcui/config.py, finds that alias's
newest amd64 build on images.lxd.canonical.com (the server LXD pulls it from), downloads
its ``rootfs.squashfs`` and checks it against the index's sha256. It writes
``image.json`` with what it fetched, and what Alpine says about its releases.

``report`` fails if Trivy didn't find the image's Alpine packages (an empty scan isn't a
clean one), then prints the body of the "Folder browser helper image" issue, or nothing
when all's well: fixable HIGH or CRITICAL vulnerabilities in today's build (Trivy's rootfs
scan, asked for only those), a newer Alpine release than the default, the default's
end of support within 90 days, or the image server's builds stalled for a week. The
sha256 check catches a broken or mismatched download; it's the same trust in the image
server that LXD itself places in it, not a signature. Helpers are made again from the
current build after ``browse_refresh_days``, so a fix in Alpine reaches them without a
release of the app; the issue says when one is needed.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SERVER = "https://images.lxd.canonical.com"
RELEASES = "https://alpinelinux.org/releases.json"
EOL_WARN_DAYS = 90
STALE_BUILD_DAYS = 7  # the image server builds daily: older means its builds have stalled
SERIAL = re.compile(r"\d{8}_\d{4}")


class NoPackages(SystemExit):
    """Trivy found no Alpine packages: the scan proves nothing, so the run fails."""


def check_scanned(report: dict[str, Any]) -> None:
    """The scan really read the image's apk database (D97 review): Alpine recognised and
    its packages listed (``--list-all-pkgs``), busybox among them. Otherwise an empty
    report would look like a clean one and close a real issue."""
    family = ((report.get("Metadata") or {}).get("OS") or {}).get("Family")
    pkgs = [
        p.get("Name")
        for r in report.get("Results") or []
        if r.get("Class") == "os-pkgs"
        for p in r.get("Packages") or []
    ]
    if family != "alpine" or "busybox" not in pkgs:
        raise NoPackages("Trivy found no Alpine packages in the image: the scan proves nothing")


def cell(text: object) -> str:
    """A value from the image or Trivy's database, safe in a Markdown table: one line, no
    table or code markup, in a code span (no @mentions or links; D97 review)."""
    clean = re.sub(r"[\r\n|`]+", " ", str(text)).strip()
    return f"`{clean}`" if clean else ""


def default_image(config: str) -> str:
    m = re.search(r'browse_image: str = "([^"]+)"', config)
    if not m:
        raise SystemExit("browse_image's default not found in config.py")
    return m.group(1)


def latest(index: dict[str, Any], alias: str, arch: str = "amd64") -> dict[str, Any]:
    """The newest build of ``alias``: its product, serial and rootfs item."""
    for name, product in index["products"].items():
        aliases = str(product.get("aliases", "")).split(",")
        if alias in aliases and product.get("arch") == arch:
            serial = max(product["versions"])
            item = product["versions"][serial]["items"]["rootfs.squashfs"]
            return {"product": name, "serial": serial, **item}
    raise SystemExit(f"{alias} ({arch}) isn't on {SERVER}")


def newest_stable(index: dict[str, Any], arch: str = "amd64") -> str | None:
    """The highest alpine/X.Y the server has."""
    found = []
    for product in index["products"].values():
        for a in str(product.get("aliases", "")).split(","):
            m = re.fullmatch(r"alpine/(\d+)\.(\d+)", a)
            if m and product.get("arch") == arch:
                found.append((int(m.group(1)), int(m.group(2))))
    return f"alpine/{'.'.join(map(str, max(found)))}" if found else None


def eol(releases: dict[str, Any], alias: str) -> str | None:
    """The end-of-support date Alpine gives the alias's release branch."""
    m = re.fullmatch(r"alpine/(\d+\.\d+)", alias)
    if not m:
        return None
    for b in releases.get("release_branches") or []:
        if b.get("rel_branch") == f"v{m.group(1)}":
            return b.get("eol_date")
    return None


def _get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as r:  # noqa: S310 - fixed https URLs
        data: bytes = r.read()
    return data


def fetch(out: Path) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    alias = default_image((ROOT / "backend/mcui/config.py").read_text())
    index = json.loads(_get(f"{SERVER}/streams/v1/images.json"))
    build = latest(index, alias)
    data = _get(f"{SERVER}/{build['path']}")
    if hashlib.sha256(data).hexdigest() != build["sha256"]:
        raise SystemExit(f"{build['path']}: sha256 doesn't match the index")
    (out / "rootfs.squashfs").write_bytes(data)
    try:
        end = eol(json.loads(_get(RELEASES)), alias)
    except (OSError, ValueError):
        end = None
    info = {
        "alias": alias,
        "serial": build["serial"],
        "newest": newest_stable(index),
        "eol": end,
    }
    (out / "image.json").write_text(json.dumps(info))
    return info


def body(report: dict[str, Any], info: dict[str, Any], today: dt.date) -> str:
    rows = sorted(
        {
            (v["VulnerabilityID"], v["PkgName"]): v
            for r in report.get("Results") or []
            for v in r.get("Vulnerabilities") or []
            if v.get("FixedVersion")
        }.values(),
        key=lambda v: (
            v.get("Severity") != "CRITICAL",
            v["PkgName"],
            v["VulnerabilityID"],
        ),
    )
    notes = []
    serial = info["serial"] if SERIAL.fullmatch(str(info.get("serial", ""))) else "unknown"
    image = f"{cell(info['alias'])} (build {serial})"
    if rows:
        notes += [
            (
                f"The weekly scan found {len(rows)} fixable HIGH or CRITICAL vulnerabilities "
                f"in {image}, today's build on images.lxd.canonical.com. Helpers are made again "
                "from the current build after `browse_refresh_days`; if the build itself is "
                "behind Alpine's fixes, move `browse_image` to a newer release."
            ),
            "",
            "| Vulnerability | Severity | Package | Installed | Fixed in |",
            "|---|---|---|---|---|",
        ]
        notes += [
            f"| {cell(v['VulnerabilityID'])} | {cell(v.get('Severity', ''))} "
            f"| {cell(v['PkgName'])} | {cell(v.get('InstalledVersion', ''))} "
            f"| {cell(v['FixedVersion'])} |"
            for v in rows
        ]
        notes.append("")
    newest = info.get("newest")
    current = _version(info["alias"])  # (0, 0): not a numbered release (edge)
    if newest and current != (0, 0) and _version(newest) > current:
        notes.append(
            f"A newer Alpine release is out: `{newest}`. Move `browse_image`'s default "
            "(backend/mcui/config.py) to it in the next release."
        )
    if serial == "unknown" or today - dt.datetime.strptime(serial[:8], "%Y%m%d").date() > (
        dt.timedelta(days=STALE_BUILD_DAYS)
    ):
        notes.append(
            f"The image server's newest build of {cell(info['alias'])} is {serial}: it "
            "normally builds daily, so helpers remade now get an old build. Check "
            "images.lxd.canonical.com, or move `browse_image` to a release that's built."
        )
    end = info.get("eol")
    if end and dt.date.fromisoformat(end) - today <= dt.timedelta(days=EOL_WARN_DAYS):
        notes.append(
            f"`{info['alias']}` reaches the end of Alpine's support on {end}: move "
            "`browse_image`'s default to a newer release before then."
        )
    return "\n".join(notes) + "\n" if notes else ""


def _version(alias: str) -> tuple[int, ...]:
    m = re.fullmatch(r"alpine/(\d+)\.(\d+)", alias)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[1] == "fetch":
        print(json.dumps(fetch(Path(argv[2]))))
        return 0
    if len(argv) == 4 and argv[1] == "report":
        report = json.loads(Path(argv[2]).read_text())
        check_scanned(report)
        info = json.loads(Path(argv[3]).read_text())
        sys.stdout.write(body(report, info, dt.date.today()))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
