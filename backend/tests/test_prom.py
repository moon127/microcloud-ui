import math

import pytest

from mcui.metrics.prom import ParseError, parse
from tests.conftest import fixture_text


def test_parse_lxd_fixture():
    exp = parse(fixture_text("metrics_m1_t0.txt"), strict=True)
    assert exp.errors == []
    assert exp.types["lxd_cpu_seconds_total"] == "counter"
    assert exp.help["lxd_go_goroutines"].startswith("Number of goroutines")
    (g,) = exp.by_name("lxd_go_goroutines")
    assert g.labels == {} and g.value == 321
    fs = exp.by_name("lxd_filesystem_size_bytes")[0]
    assert fs.labels["mountpoint"] == "/" and fs.value == 1e10


def test_labels_escapes_and_spacing():
    exp = parse('m{a="x\\"y", b="back\\\\slash",c="new\\nline",d="\\q",} 1.5 1700000000000\n')
    (s,) = exp.samples
    assert s.labels == {"a": 'x"y', "b": "back\\slash", "c": "new\nline", "d": "\\q"}
    assert s.value == 1.5
    assert s.timestamp_ms == 1700000000000


def test_special_values_and_empty_labels():
    exp = parse("a NaN\nb +Inf\nc -Inf\nd{} 2\n")
    v = {s.name: s.value for s in exp.samples}
    assert math.isnan(v["a"]) and v["b"] == math.inf and v["c"] == -math.inf and v["d"] == 2


def test_comments_and_blank_lines():
    exp = parse("\n# just a comment\n# HELP x\n# TYPE y gauge\ny 1\n")
    assert exp.help == {"x": ""}
    assert exp.types == {"y": "gauge"}


@pytest.mark.parametrize(
    "line,msg",
    [
        ('{a="b"} 1', "missing metric name"),
        ("m", "expected value"),
        ("m 1 2 3", "expected value"),
        ("m abc", "bad value"),
        ("m 1 notats", "bad timestamp"),
        ('m{a="b"', "unterminated label set"),
        ("m{a} 1", "label without"),
        ("m{a=b} 1", "not quoted"),
        ('m{a="b} 1', "unterminated label value"),
    ],
)
def test_malformed(line, msg):
    exp = parse(f"ok 1\n{line}\n")
    assert len(exp.samples) == 1
    assert msg in exp.errors[0] and "line 2" in exp.errors[0]
    with pytest.raises(ParseError, match=msg):
        parse(line, strict=True)
