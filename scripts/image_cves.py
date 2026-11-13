"""Fixable CVEs in the published image, as the body of a "Rebuild needed" issue (D76).
Run by .github/workflows/security.yml on Trivy's JSON report:

    python scripts/image_cves.py trivy.json ghcr.io/moon127/microcloud-ui:latest

Prints Markdown listing each fixable HIGH or CRITICAL vulnerability (Trivy was asked for
only those, with ``--ignore-unfixed``), or nothing when there are none. Ubuntu packages and
the Python packages are both fixed by a rebuild: the image installs current versions.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def findings(report: dict[str, Any]) -> list[dict[str, str]]:
    """One row per (package, vulnerability), most severe first."""
    rows = {
        (v["PkgName"], v["VulnerabilityID"]): {
            "id": v["VulnerabilityID"],
            "severity": v.get("Severity", ""),
            "package": v["PkgName"],
            "installed": v.get("InstalledVersion", ""),
            "fixed": v.get("FixedVersion", ""),
            "target": r.get("Target", ""),
        }
        for r in report.get("Results") or []
        for v in r.get("Vulnerabilities") or []
        if v.get("FixedVersion")
    }
    order = {"CRITICAL": 0, "HIGH": 1}
    return sorted(
        rows.values(),
        key=lambda x: (order.get(x["severity"], 2), x["package"], x["id"]),
    )


def body(rows: list[dict[str, str]], image: str) -> str:
    if not rows:
        return ""
    lines = [
        (
            f"The weekly scan found {len(rows)} fixable HIGH or CRITICAL vulnerabilities in "
            f"`{image}`. A patch release rebuilds the image and bundles with the fixed "
            'versions (docs/RELEASING.md, "Security updates").'
        ),
        "",
        "| Vulnerability | Severity | Package | Installed | Fixed in | Where |",
        "|---|---|---|---|---|---|",
    ]
    lines += [
        f"| {r['id']} | {r['severity']} | {r['package']} | {r['installed']} | {r['fixed']} "
        f"| {r['target']} |"
        for r in rows
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    report = json.loads(Path(argv[1]).read_text())
    sys.stdout.write(body(findings(report), argv[2]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
