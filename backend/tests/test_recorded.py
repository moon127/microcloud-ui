"""Parse metrics recorded from a real cluster (D26); skipped until fixtures exist."""

from pathlib import Path

import pytest

from mcui.metrics.live import instance_live, instance_totals
from mcui.metrics.prom import parse

RECORDED = sorted((Path(__file__).parent / "fixtures" / "recorded").glob("metrics_*.txt"))


@pytest.mark.skipif(not RECORDED, reason="no recorded fixtures (mcui record-fixtures)")
@pytest.mark.parametrize("path", RECORDED, ids=lambda p: p.stem)
def test_recorded_metrics_parse(path):
    exp = parse(path.read_text(), strict=True)
    totals = instance_totals(exp)
    for key, t in totals.items():
        live = instance_live("m", key, t, t, 15)
        assert live.cpus, f"{key}: no CPU count"
        assert live.cpu_percent == 0
