import math

import pytest

from mcui.metrics.store import (
    H1,
    M1,
    RAW,
    MetricStore,
    Sample,
    SeriesData,
    SQLiteMetricStore,
    aggregate,
    decode,
    encode,
    rates,
    rollup_buckets,
    rollup_points,
)

DAY = 86400
T0 = 1_700_000_000 - 1_700_000_000 % DAY  # midnight UTC


class Clock:
    def __init__(self, t: float = T0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def db(clock):
    s = SQLiteMetricStore(":memory:", max_gap=60, clock=clock)
    yield s
    s.close()


def counter(v, **labels):
    return Sample("c_total", {"inst": "a", **labels}, v, "counter")


def gauge(v, **labels):
    return Sample("g", {"inst": "a", **labels}, v)


def test_encode_roundtrip():
    ts = [T0, T0 + 15, T0 + 30, T0 + 47]
    cols = [[1.5, 2.0, -3.25, 1e300], [0.0, math.inf, 5.0, 5.0]]
    assert decode(encode(ts, cols)) == (ts, cols)
    assert decode(encode([], [[]])) == ([], [[]])


def test_rollup_points_counter_reset_and_gap():
    pts = [(0, 10.0), (15, 20.0), (30, 5.0), (45, 15.0), (60, 25.0), (500, 30.0)]
    b = rollup_points(pts, prev=(-15, 4.0), step=60, max_gap=60)
    assert b[0] == (0, 12.5, 20.0, 6 + 10 + 5 + 10)  # reset at 30 counts 5
    assert b[1] == (60, 25.0, 25.0, 10.0)
    assert b[2] == (480, 30.0, 30.0, 0.0)  # 440 s gap: increase not counted
    assert rollup_points([(0, 1.0)], None, 60, 60) == [(0, 1.0, 1.0, 0.0)]


def test_rollup_buckets_and_rates():
    rows = [(0, 1.0, 2.0, 3.0), (60, 3.0, 9.0, 4.0), (3600, 5.0, 5.0, 1.0)]
    assert rollup_buckets(rows, 3600) == [(0, 2.0, 9.0, 7.0), (3600, 5.0, 5.0, 1.0)]
    assert rates([(0, 10.0), (15, 40.0), (30, 4.0), (200, 10.0)], 60) == [(15, 2.0), (30, 4 / 15)]


def test_aggregate_aligns_to_step():
    s = [
        SeriesData({"a": "x", "dev": "1"}, [(0, 1.0), (16, 2.0)]),
        SeriesData({"a": "x", "dev": "2"}, [(3, 10.0)]),
        SeriesData({"a": "y"}, [(0, 5.0)]),
    ]
    out = aggregate(s, ["a"], 15)
    assert [(x.labels, x.points) for x in out] == [
        ({"a": "x"}, [(0, 11.0), (15, 2.0)]),
        ({"a": "y"}, [(0, 5.0)]),
    ]


def test_write_query_raw(db):
    for i in range(5):
        db.write(
            T0 + 15 * i, [counter(100.0 * i), gauge(i), counter(1.0, inst="b"), gauge(math.nan)]
        )
    assert db.names() == [
        {"name": "c_total", "kind": "counter", "series": 2},
        {"name": "g", "kind": "gauge", "series": 1},
    ]
    assert [lab for _, lab, _ in db.find_series("c_total", {"inst": "b"})] == [{"inst": "b"}]
    r = db.query("g", None, T0, T0 + 3600, res=RAW)
    assert r.kind == "gauge" and r.series[0].points == [(T0 + 15 * i, float(i)) for i in range(5)]
    r = db.query("c_total", {"inst": "a"}, T0 + 15, T0 + 3600, fn="rate", res=RAW)
    assert r.series[0].points == [(T0 + 15 * i, 100 / 15) for i in range(1, 5)]
    assert db.query("nope", None, T0, T0 + 60).series == []
    assert db.query("nope", None, T0, T0 + 60).kind == "gauge"


def fill(db, hours, step=15, inst="a"):
    """A counter rising 30/s and a gauge equal to the minute of the hour."""
    for t in range(T0, T0 + hours * 3600, step):
        db.write(t, [counter(2.0 * (t - T0), inst=inst), gauge(((t - T0) // 60) % 60, inst=inst)])


def test_compact_packs_hours_and_rollups(db, clock):
    fill(db, 3)
    clock.t = T0 + 2 * 3600 + 10  # hours 0 and 1 finished
    st = db.compact()
    assert st["packed"] == 4  # 2 series x 2 hours
    stats = db.stats()
    assert stats["chunks"] == {"0": 4, "60": 4} and stats["head_rows"] == 2 * 240
    assert stats["series"] == 2 and stats["db_bytes"] > 0

    raw = db.query("g", None, T0, T0 + 3 * 3600, res=RAW).series[0].points
    assert len(raw) == 3 * 240 and raw[0] == (T0, 0.0)  # chunks + head stitched
    rate = db.query("c_total", None, T0, T0 + 3 * 3600, fn="rate", res=RAW).series[0].points
    assert {v for _, v in rate} == {2.0} and len(rate) == 3 * 240 - 1

    m1 = db.query("c_total", None, T0, T0 + 3 * 3600, fn="rate", res=M1).series[0].points
    assert len(m1) == 180
    # first minute has no previous point; every later minute (incl. hour edges and
    # the unpacked third hour) sees 4 x 30 = 120 per 60 s
    assert m1[0] == (T0, 1.5) and {v for _, v in m1[1:]} == {2.0}
    g1 = db.query("g", None, T0, T0 + 3 * 3600, res=M1).series[0].points
    assert g1[61] == (T0 + 61 * 60, 1.0)
    gmax = db.query("g", None, T0, T0 + 3 * 3600, fn="max", res=M1).series[0].points
    assert gmax[5][1] == 5.0

    h1 = db.query("c_total", None, T0, T0 + 3 * 3600, fn="rate", res=H1).series[0].points
    assert [t for t, _ in h1] == [T0, T0 + 3600, T0 + 7200]
    assert h1[1][1] == 2.0
    gh = db.query("g", None, T0, T0 + 3 * 3600, res=H1).series[0].points
    assert gh[0] == (T0, 29.5)


def test_compact_is_idempotent_and_merges_late_points(db, clock):
    fill(db, 1)
    clock.t = T0 + 3600
    db.compact()
    db.write(T0 + 7, [gauge(99.0)])  # late point for the packed hour
    clock.t = T0 + 3700
    assert db.compact()["packed"] == 1
    pts = db.query("g", None, T0, T0 + 3600, res=RAW).series[0].points
    assert pts[:2] == [(T0, 0.0), (T0 + 7, 99.0)] and len(pts) == 241
    assert db.compact()["packed"] == 0


def test_days_built_and_retention(clock):
    db = SQLiteMetricStore(
        ":memory:", retention={RAW: 3600, M1: DAY, H1: 3 * DAY}, max_gap=60, clock=clock
    )
    fill(db, 26, step=60)
    clock.t = T0 + 26 * 3600
    st = db.compact()
    assert st["days"] == 1
    assert db.stats()["chunks"]["3600"] == 2
    h1 = db.query("c_total", None, T0, T0 + DAY, fn="rate", res=H1).series[0].points
    assert len(h1) == 24 and {round(v, 6) for _, v in h1[1:]} == {2.0}
    # raw older than 1 h and m1 older than a day are dropped
    clock.t = T0 + DAY + 3 * 3600
    st = db.compact()
    assert st["dropped"] > 0
    assert db.query("g", None, T0, T0 + 3600, res=RAW).series[0].points == []
    assert db.query("g", None, T0, T0 + 3600, res=H1).series[0].points[0][0] == T0
    # auto resolution follows retention
    now = int(clock.t)
    assert db.auto_resolution(now - 600, now) == RAW
    assert db.auto_resolution(now - 7200, now) == M1
    assert db.auto_resolution(now - 2 * DAY, now) == H1
    assert db.query("g", None, now - 2 * DAY, now).resolution == H1
    # everything expires; series without data are garbage-collected daily
    clock.t = T0 + 10 * DAY
    db.compact()
    assert db.stats()["series"] == 0
    db.write(int(clock.t), [gauge(1.0)])  # id cache was cleared, so this re-creates it
    assert db.stats()["series"] == 1
    db.close()


def test_h1_from_m1_when_day_not_built(db, clock):
    fill(db, 2, step=60)
    clock.t = T0 + 2 * 3600
    db.compact()  # m1 chunks exist, no h1 yet (day not over)
    pts = db.query("c_total", None, T0, T0 + 7200, fn="rate", res=H1).series[0].points
    assert [t for t, _ in pts] == [T0, T0 + 3600]


def test_query_by_sums_series(db):
    for t in (T0, T0 + 15):
        db.write(t, [counter(t - T0, inst="a", dev="x"), counter(2 * (t - T0), inst="a", dev="y")])
    r = db.query("c_total", None, T0, T0 + 60, fn="rate", res=RAW, by=["inst"])
    assert [(s.labels, s.points) for s in r.series] == [({"inst": "a"}, [(T0 + 15, 3.0)])]


async def test_async_facade(tmp_path):
    store = MetricStore.open(tmp_path, retention={RAW: DAY, M1: DAY, H1: DAY}, max_gap=60)
    now = int(store.backend.clock())
    assert await store.write(now, [gauge(1.0)]) == 1
    assert (await store.query("g", None, now - 60, now + 1)).series[0].points == [(now, 1.0)]
    assert (await store.names())[0]["name"] == "g"
    assert len(await store.find_series("g")) == 1
    assert (await store.stats())["series"] == 1
    assert (await store.compact())["packed"] == 0
    await store.aclose()
    assert (tmp_path / "metrics.db").is_file()
