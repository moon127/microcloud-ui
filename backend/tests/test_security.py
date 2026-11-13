"""Dependency and image vulnerability checks (D76): the workflows and the issue script."""

import importlib.util
import json
import re
from pathlib import Path

import yaml

from tests.test_release import workflow

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("image_cves", ROOT / "scripts/image_cves.py")
assert _spec and _spec.loader
image_cves = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(image_cves)

# the shape of `trivy image --format json` (Trivy 0.5x), trimmed
REPORT = {
    "Results": [
        {
            "Target": "ghcr.io/moon127/microcloud-ui:latest (ubuntu 26.04)",
            "Vulnerabilities": [
                {
                    "VulnerabilityID": "CVE-2026-0001",
                    "PkgName": "libssl3t64",
                    "InstalledVersion": "3.5.0-1",
                    "FixedVersion": "3.5.0-1ubuntu0.1",
                    "Severity": "HIGH",
                },
                {
                    "VulnerabilityID": "CVE-2026-0002",
                    "PkgName": "zlib1g",
                    "InstalledVersion": "1.3",
                    "Severity": "CRITICAL",  # no fix yet: not listed
                },
            ],
        },
        {
            "Target": "Python",
            "Vulnerabilities": [
                {
                    "VulnerabilityID": "GHSA-xxxx",
                    "PkgName": "starlette",
                    "InstalledVersion": "0.40.0",
                    "FixedVersion": "0.40.1",
                    "Severity": "CRITICAL",
                }
            ],
        },
        {"Target": "frontend", "Vulnerabilities": None},
    ]
}


def test_fixable_findings_most_severe_first():
    rows = image_cves.findings(REPORT)
    assert [(r["severity"], r["package"]) for r in rows] == [
        ("CRITICAL", "starlette"),
        ("HIGH", "libssl3t64"),
    ]
    assert image_cves.findings({}) == []


def test_issue_body(tmp_path, capsys):
    body = image_cves.body(image_cves.findings(REPORT), "img:latest")
    assert "found 2 fixable HIGH or CRITICAL vulnerabilities in `img:latest`" in body
    assert "| GHSA-xxxx | CRITICAL | starlette | 0.40.0 | 0.40.1 | Python |" in body
    assert image_cves.body([], "img") == ""
    report = tmp_path / "trivy.json"
    report.write_text(json.dumps({"Results": []}))
    assert image_cves.main(["x", str(report), "img"]) == 0
    assert capsys.readouterr().out == ""  # clean: the workflow closes the issue
    assert image_cves.main(["x"]) == 2


def test_audit_blocks_ci_and_releases():
    ci = workflow("ci.yml")
    assert "make install audit" in [s.get("run") for s in ci["jobs"]["audit"]["steps"]]
    make = (ROOT / "Makefile").read_text()
    assert "pip-audit --progress-spinner off backend/" in make  # runtime deps only
    assert "npm audit --omit=dev --audit-level=high" in make  # what ships, high and up


def test_weekly_scan():
    sec = workflow("security.yml")
    assert sec["on"]["schedule"] and "workflow_dispatch" in sec["on"]
    assert sec["permissions"] == {"contents": "read"}
    image = sec["jobs"]["image"]
    assert image["permissions"]["issues"] == "write"
    trivy = [s for s in image["steps"] if "trivy-action" in s.get("uses", "")]
    # a moved tag can't change what runs (trivy-action's tags were rewritten in 2026)
    assert all(re.fullmatch(r"aquasecurity/trivy-action@[0-9a-f]{40}", s["uses"]) for s in trivy)
    scans = [s["with"] for s in trivy]
    assert all(w["ignore-unfixed"] is True and w["severity"] == "HIGH,CRITICAL" for w in scans)
    assert image["env"]["IMAGE"] == "ghcr.io/moon127/microcloud-ui:latest"
    issue = image["steps"][-1]["run"]
    assert "scripts/image_cves.py trivy.json" in issue and "gh issue close" in issue


def test_dependabot():
    cfg = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text())
    assert {(u["package-ecosystem"], u["directory"]) for u in cfg["updates"]} == {
        ("npm", "/frontend"),
        ("pip", "/backend"),
        ("github-actions", "/"),
    }
    assert all(u["schedule"]["interval"] == "weekly" and u["groups"] for u in cfg["updates"])
    # majors are planned upgrades; security updates (repository setting) still bring them
    major = {"dependency-name": "*", "update-types": ["version-update:semver-major"]}
    assert all(u["ignore"] == [major] for u in cfg["updates"])


def test_image_drops_pip():
    """Only needed to install; the image shouldn't carry python3-venv's copy."""
    containerfile = (ROOT / "deploy/container/Containerfile").read_text()
    assert "/opt/mcui/bin/python -m pip uninstall --yes --quiet pip" in containerfile
