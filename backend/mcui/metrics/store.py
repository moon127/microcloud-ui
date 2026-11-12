"""Metric history in SQLite (D8, D9, D36).

New samples land in a ``head`` table. Once an hour has ended, ``compact`` packs
each series' raw points for that hour into one zlib-compressed chunk and writes
the matching 1-minute rollup chunk; after each day, 1-hour rollups are built
from the 1-minute ones. Old chunks are dropped per retention. Rollup points
keep ``avg``, ``max`` and ``increase`` (counter increase with resets handled),
so rates over any resolution are ``increase / step``. Queries read packed
chunks and fill anything not yet packed from the finer level on the fly.
"""

from __future__ import annotations

import asyncio
import json
import math
import sqlite3
import struct
import time
import zlib
from array import array
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any, Literal, ParamSpec, TypeVar

from mcui.files import private_db

Kind = Literal["counter", "gauge"]
Fn = Literal["value", "rate", "max"]

RAW, M1, H1 = 0, 60, 3600
RESOLUTIONS = (RAW, M1, H1)
SPAN = {RAW: 3600, M1: 3600, H1: 86400}  # seconds covered by one chunk
FINER = {M1: RAW, H1: M1}

Point = tuple[int, float]
Bucket = tuple[int, float, float, float]  # ts, avg, max, increase

SCHEMA = """
CREATE TABLE IF NOT EXISTS series (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    labels TEXT NOT NULL,
    kind TEXT NOT NULL,
    UNIQUE (name, labels)
);
CREATE TABLE IF NOT EXISTS head (
    series_id INTEGER NOT NULL,
    ts INTEGER NOT NULL,
    value REAL NOT NULL,
    PRIMARY KEY (series_id, ts)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS chunks (
    series_id INTEGER NOT NULL,
    res INTEGER NOT NULL,
    start INTEGER NOT NULL,
    end INTEGER NOT NULL,
    last_ts INTEGER,
    last REAL,
    data BLOB NOT NULL,
    PRIMARY KEY (series_id, res, start)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS chunks_res_end ON chunks (res, end);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


@dataclass(frozen=True)
class Sample:
    name: str
    labels: Mapping[str, str]
    value: float
    kind: Kind = "gauge"


@dataclass
class SeriesData:
    labels: dict[str, str]
    points: list[Point] = field(default_factory=list)


@dataclass
class QueryResult:
    name: str
    kind: Kind
    resolution: int
    fn: Fn
    series: list[SeriesData]

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "resolution": self.resolution,
            "fn": self.fn,
            "series": [{"labels": s.labels, "points": s.points} for s in self.series],
        }


# --- chunk encoding --------------------------------------------------------


def _xor(values: Sequence[float]) -> bytes:
    q = array("Q")
    q.frombytes(array("d", values).tobytes())
    out = array("Q")
    prev = 0
    for x in q:
        out.append(x ^ prev)
        prev = x
    return out.tobytes()


def _unxor(data: bytes) -> list[float]:
    q = array("Q")
    q.frombytes(data)
    out = array("Q")
    prev = 0
    for x in q:
        prev ^= x
        out.append(prev)
    d = array("d")
    d.frombytes(out.tobytes())
    return d.tolist()


def encode(ts: Sequence[int], cols: Sequence[Sequence[float]]) -> bytes:
    """Delta-encoded timestamps + XOR-delta float columns, zlib-compressed."""
    deltas = array("q", (t - p for t, p in zip(ts, [0, *ts], strict=False)))
    body = struct.pack("<II", len(ts), len(cols)) + deltas.tobytes()
    body += b"".join(_xor(c) for c in cols)
    return zlib.compress(body, 6)


def decode(blob: bytes) -> tuple[list[int], list[list[float]]]:
    body = zlib.decompress(blob)
    n, k = struct.unpack_from("<II", body)
    off = 8
    deltas = array("q")
    deltas.frombytes(body[off : off + 8 * n])
    off += 8 * n
    ts: list[int] = []
    acc = 0
    for d in deltas:
        acc += d
        ts.append(acc)
    cols = []
    for _ in range(k):
        cols.append(_unxor(body[off : off + 8 * n]))
        off += 8 * n
    return ts, cols


# --- rollups and rates -----------------------------------------------------


def _delta(prev: float, cur: float) -> float:
    return cur - prev if cur >= prev else cur  # a decrease is a counter reset


def rollup_points(
    points: Iterable[Point], prev: Point | None, step: int, max_gap: int
) -> list[Bucket]:
    """Raw points -> buckets of ``step`` seconds. ``prev`` precedes ``points``."""
    acc: dict[int, list[float]] = {}
    last = prev
    for ts, v in points:
        a = acc.setdefault(ts - ts % step, [0.0, 0.0, -math.inf, 0.0])
        a[0] += v
        a[1] += 1
        a[2] = max(a[2], v)
        if last is not None and 0 < ts - last[0] <= max_gap:
            a[3] += _delta(last[1], v)
        last = (ts, v)
    return [(b, s / n, mx, inc) for b, (s, n, mx, inc) in sorted(acc.items())]


def rollup_buckets(rows: Iterable[Bucket], step: int) -> list[Bucket]:
    acc: dict[int, list[float]] = {}
    for ts, avg, mx, inc in rows:
        a = acc.setdefault(ts - ts % step, [0.0, 0.0, -math.inf, 0.0])
        a[0] += avg
        a[1] += 1
        a[2] = max(a[2], mx)
        a[3] += inc
    return [(b, s / n, mx, inc) for b, (s, n, mx, inc) in sorted(acc.items())]


def rates(points: Sequence[Point], max_gap: int) -> list[Point]:
    out = []
    for (t0, v0), (t1, v1) in zip(points, points[1:], strict=False):
        if 0 < t1 - t0 <= max_gap:
            out.append((t1, _delta(v0, v1) / (t1 - t0)))
    return out


def _labels_key(labels: Mapping[str, str]) -> str:
    return json.dumps(dict(sorted(labels.items())), separators=(",", ":"))


# --- the store -------------------------------------------------------------


class SQLiteMetricStore:
    """Synchronous store; use from one thread (``MetricStore`` does that)."""

    def __init__(
        self,
        path: Path | str,
        *,
        retention: Mapping[int, int] | None = None,
        max_gap: int = 60,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if path != ":memory:":
            private_db(Path(path))
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        if path != ":memory:":
            self.db.execute("PRAGMA journal_mode = WAL")
            self.db.execute("PRAGMA synchronous = NORMAL")
        self.db.executescript(SCHEMA)
        self.retention = dict(retention or {RAW: 86400, M1: 14 * 86400, H1: 365 * 86400})
        self.max_gap = max_gap
        self.clock = clock
        self._ids: dict[tuple[str, str], int] = {}

    def close(self) -> None:
        self.db.close()

    # writing

    def _series_id(self, name: str, labels: Mapping[str, str], kind: str) -> int:
        key = (name, _labels_key(labels))
        sid = self._ids.get(key)
        if sid is None:
            self.db.execute(
                "INSERT OR IGNORE INTO series (name, labels, kind) VALUES (?,?,?)", (*key, kind)
            )
            row = self.db.execute(
                "SELECT id FROM series WHERE name = ? AND labels = ?", key
            ).fetchone()
            sid = self._ids[key] = int(row[0])
        return sid

    def write(self, ts: int, samples: Iterable[Sample]) -> int:
        rows = [
            (self._series_id(s.name, s.labels, s.kind), ts, s.value)
            for s in samples
            if math.isfinite(s.value)
        ]
        self.db.execute("BEGIN")
        self.db.executemany("INSERT OR REPLACE INTO head VALUES (?,?,?)", rows)
        self.db.execute("COMMIT")
        return len(rows)

    # compaction

    def _meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return str(row[0]) if row else None

    def _set_meta(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, value))

    def _prev_raw(self, sid: int, before: int) -> Point | None:
        row = self.db.execute(
            "SELECT last_ts, last FROM chunks WHERE series_id = ? AND res = 0 AND start < ? "
            "ORDER BY start DESC LIMIT 1",
            (sid, before),
        ).fetchone()
        return (int(row[0]), float(row[1])) if row else None

    def _put(self, sid: int, res: int, start: int, ts: list[int], cols: list[list[float]]) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO chunks VALUES (?,?,?,?,?,?,?)",
            (sid, res, start, start + SPAN[res], ts[-1], cols[0][-1], encode(ts, cols)),
        )

    def _put_buckets(self, sid: int, res: int, start: int, rows: list[Bucket]) -> None:
        self._put(sid, res, start, [r[0] for r in rows], [[r[i] for r in rows] for i in (1, 2, 3)])

    def compact(self, now: float | None = None) -> dict[str, int]:
        """Pack finished hours, build finished days, apply retention."""
        now_i = int(self.clock() if now is None else now)
        hour_end = now_i - now_i % 3600
        stats = {"packed": 0, "days": 0, "dropped": 0}
        rows = self.db.execute(
            "SELECT series_id, ts, value FROM head WHERE ts < ? ORDER BY series_id, ts", (hour_end,)
        ).fetchall()
        groups: dict[tuple[int, int], list[Point]] = {}
        for sid, ts, v in rows:
            groups.setdefault((sid, ts - ts % 3600), []).append((ts, v))
        self.db.execute("BEGIN")
        for (sid, hour), pts in groups.items():
            old = self.db.execute(
                "SELECT data FROM chunks WHERE series_id = ? AND res = 0 AND start = ?", (sid, hour)
            ).fetchone()
            if old is not None:  # late points for an hour already packed: merge
                ots, (ov,) = decode(old[0])
                pts = sorted({**dict(zip(ots, ov, strict=True)), **dict(pts)}.items())
            self._put(sid, RAW, hour, [p[0] for p in pts], [[p[1] for p in pts]])
            prev = self._prev_raw(sid, hour)
            self._put_buckets(sid, M1, hour, rollup_points(pts, prev, M1, self.max_gap))
            stats["packed"] += 1
        self.db.execute("DELETE FROM head WHERE ts < ?", (hour_end,))
        stats["days"] = self._build_days(now_i - now_i % 86400)
        for res in RESOLUTIONS:
            cur = self.db.execute(
                "DELETE FROM chunks WHERE res = ? AND end <= ?", (res, now_i - self.retention[res])
            )
            stats["dropped"] += cur.rowcount
        self.db.execute("DELETE FROM head WHERE ts < ?", (now_i - self.retention[RAW],))
        self.db.execute("COMMIT")
        if int(self._meta("series_gc") or 0) + 86400 <= now_i:
            self.gc_series()
            self._set_meta("series_gc", str(now_i))
        return stats

    def _build_days(self, day_end: int) -> int:
        done = self._meta("h1_done")
        if done is None:
            row = self.db.execute("SELECT MIN(start) FROM chunks WHERE res = ?", (M1,)).fetchone()
            first = day_end if row[0] is None else int(row[0])
            start = first - first % 86400
        else:
            start = int(done)
        n = 0
        for day in range(start, day_end, 86400):
            per: dict[int, list[Bucket]] = {}
            for sid, data in self.db.execute(
                "SELECT series_id, data FROM chunks WHERE res = ? AND start >= ? AND start < ? "
                "ORDER BY series_id, start",
                (M1, day, day + 86400),
            ):
                ts, (avg, mx, inc) = decode(data)
                per.setdefault(sid, []).extend(zip(ts, avg, mx, inc, strict=True))
            for sid, buckets in per.items():
                self._put_buckets(sid, H1, day, rollup_buckets(buckets, H1))
            n += 1
        self._set_meta("h1_done", str(max(start, day_end)))
        return n

    def gc_series(self) -> int:
        cur = self.db.execute(
            "DELETE FROM series WHERE NOT EXISTS (SELECT 1 FROM head h WHERE h.series_id = id) "
            "AND NOT EXISTS (SELECT 1 FROM chunks c WHERE c.series_id = id)"
        )
        self._ids.clear()
        return cur.rowcount

    # reading

    def names(self) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT name, kind, COUNT(*) FROM series GROUP BY name, kind ORDER BY name"
        )
        return [{"name": n, "kind": k, "series": c} for n, k, c in rows]

    def find_series(
        self, name: str, match: Mapping[str, str] | None = None
    ) -> list[tuple[int, dict[str, str], Kind]]:
        out = []
        for sid, labels, kind in self.db.execute(
            "SELECT id, labels, kind FROM series WHERE name = ? ORDER BY labels", (name,)
        ):
            lab = json.loads(labels)
            if all(lab.get(k) == v for k, v in (match or {}).items()):
                out.append((int(sid), lab, kind))
        return out

    def raw_points(self, sid: int, start: int, end: int) -> list[Point]:
        pts: list[Point] = []
        for (data,) in self.db.execute(
            "SELECT data FROM chunks WHERE series_id = ? AND res = 0 AND start < ? AND end > ? "
            "ORDER BY start",
            (sid, end, start),
        ):
            ts, (vals,) = decode(data)
            pts += [(t, v) for t, v in zip(ts, vals, strict=True) if start <= t < end]
        pts += [
            (int(t), float(v))
            for t, v in self.db.execute(
                "SELECT ts, value FROM head WHERE series_id = ? AND ts >= ? AND ts < ? ORDER BY ts",
                (sid, start, end),
            )
        ]
        return pts

    def buckets(self, sid: int, res: int, start: int, end: int) -> list[Bucket]:
        start -= start % res
        out: list[Bucket] = []
        covered = start
        for cend, data in self.db.execute(
            "SELECT end, data FROM chunks "
            "WHERE series_id = ? AND res = ? AND start < ? AND end > ? ORDER BY start",
            (sid, res, end, start),
        ):
            ts, (avg, mx, inc) = decode(data)
            out += [b for b in zip(ts, avg, mx, inc, strict=True) if start <= b[0] < end]
            covered = max(covered, int(cend))
        if covered < end:
            fine = FINER[res]
            if fine == RAW:
                pts = self.raw_points(sid, covered - self.max_gap, end)
                before = [p for p in pts if p[0] < covered]
                rest = [p for p in pts if p[0] >= covered]
                out += rollup_points(rest, before[-1] if before else None, res, self.max_gap)
            else:
                out += rollup_buckets(self.buckets(sid, fine, covered, end), res)
        return out

    def auto_resolution(self, start: int, now: int) -> int:
        age = now - start
        if age <= self.retention[RAW]:
            return RAW
        if age <= self.retention[M1]:
            return M1
        return H1

    def query(
        self,
        name: str,
        match: Mapping[str, str] | None,
        start: int,
        end: int,
        *,
        fn: Fn = "value",
        res: int | None = None,
        by: Sequence[str] | None = None,
        step: int = 15,
    ) -> QueryResult:
        if res is None:
            res = self.auto_resolution(start, int(self.clock()))
        found = self.find_series(name, match)
        kind: Kind = found[0][2] if found else "gauge"
        series: list[SeriesData] = []
        for sid, labels, _ in found:
            if res == RAW:
                if fn == "rate":
                    pts = [
                        p
                        for p in rates(
                            self.raw_points(sid, start - self.max_gap, end), self.max_gap
                        )
                        if p[0] >= start
                    ]
                else:
                    pts = self.raw_points(sid, start, end)
            else:
                col = {"value": 1, "max": 2, "rate": 3}[fn]
                pts = [
                    (b[0], b[col] / res if fn == "rate" else b[col])
                    for b in self.buckets(sid, res, start, end)
                ]
            series.append(SeriesData(labels, pts))
        if by is not None:
            series = aggregate(series, by, res or step)
        return QueryResult(name, kind, res, fn, series)

    def stats(self) -> dict[str, Any]:
        q = self.db.execute
        chunks = {
            str(r): int(n) for r, n in q("SELECT res, COUNT(*) FROM chunks GROUP BY res").fetchall()
        }
        page = q("PRAGMA page_size").fetchone()[0] * q("PRAGMA page_count").fetchone()[0]
        return {
            "series": q("SELECT COUNT(*) FROM series").fetchone()[0],
            "head_rows": q("SELECT COUNT(*) FROM head").fetchone()[0],
            "chunks": chunks,
            "db_bytes": int(page),
        }


def aggregate(series: list[SeriesData], by: Sequence[str], step: int) -> list[SeriesData]:
    """Sum series sharing the ``by`` labels, aligning timestamps to ``step``."""
    groups: dict[tuple[str, ...], dict[int, float]] = {}
    for s in series:
        key = tuple(s.labels.get(k, "") for k in by)
        acc = groups.setdefault(key, {})
        for ts, v in s.points:
            t = ts - ts % step
            acc[t] = acc.get(t, 0.0) + v
    return [
        SeriesData(dict(zip(by, key, strict=True)), sorted(acc.items()))
        for key, acc in sorted(groups.items())
    ]


P = ParamSpec("P")
T = TypeVar("T")


class MetricStore:
    """Async facade: every call runs on one dedicated thread (D8 pluggable store)."""

    def __init__(self, backend: SQLiteMetricStore) -> None:
        self.backend = backend
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mcui-metrics")

    @classmethod
    def open(cls, data_dir: Path, *, retention: Mapping[int, int], max_gap: int) -> MetricStore:
        return cls(SQLiteMetricStore(data_dir / "metrics.db", retention=retention, max_gap=max_gap))

    async def call(self, func: Callable[P, T], /, *args: P.args, **kwargs: P.kwargs) -> T:
        return await asyncio.get_running_loop().run_in_executor(
            self._pool, partial(func, *args, **kwargs)
        )

    async def write(self, ts: int, samples: Iterable[Sample]) -> int:
        return await self.call(self.backend.write, ts, list(samples))

    async def compact(self) -> dict[str, int]:
        return await self.call(self.backend.compact)

    async def query(
        self, name: str, match: Mapping[str, str] | None, start: int, end: int, **kw: Any
    ) -> QueryResult:
        return await self.call(self.backend.query, name, match, start, end, **kw)

    async def names(self) -> list[dict[str, Any]]:
        return await self.call(self.backend.names)

    async def find_series(
        self, name: str, match: Mapping[str, str] | None = None
    ) -> list[tuple[int, dict[str, str], Kind]]:
        return await self.call(self.backend.find_series, name, match)

    async def stats(self) -> dict[str, Any]:
        return await self.call(self.backend.stats)

    async def aclose(self) -> None:
        await self.call(self.backend.close)
        self._pool.shutdown(wait=True)
