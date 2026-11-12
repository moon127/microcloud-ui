"""Parse metrics recorded from a real cluster (D26); skipped until fixtures exist."""

from pathlib import Path

import pytest

from mcui.metrics.live import instance_live, instance_totals
from mcui.metrics.prom import parse

FIXTURES = Path(__file__).parent / "fixtures"
# the first recording (2026-11-05) and the full LXD 5.21 baseline taken before the
# MicroCloud 3.4 upgrade (2026-11-12, Phase 5.0)
RECORDED = sorted(
    p for d in ("recorded", "recorded-5.21") for p in (FIXTURES / d).glob("metrics_*.txt")
)


@pytest.mark.skipif(not RECORDED, reason="no recorded fixtures (mcui record-fixtures)")
@pytest.mark.parametrize("path", RECORDED, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_recorded_metrics_parse(path):
    exp = parse(path.read_text(), strict=True)
    totals = instance_totals(exp)
    for key, t in totals.items():
        live = instance_live("m", key, t, t, 15)
        assert live.cpus, f"{key}: no CPU count"
        assert live.cpu_percent == 0


BASELINE = FIXTURES / "recorded-5.21"


@pytest.mark.parametrize("path", sorted(BASELINE.glob("*.txt")), ids=lambda p: p.stem)
def test_baseline_exporters_parse(path):
    """The Ceph mgr and node_exporter samples parse as the app scrapes them."""
    exp = parse(path.read_text(), strict=True)
    prefix = {"ceph": "ceph_", "node": "node_", "metrics": "lxd_"}[path.stem.split("_")[0]]
    assert any(s.name.startswith(prefix) for s in exp.samples)
