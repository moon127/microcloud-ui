"""Parser for the Prometheus text exposition format (version 0.0.4)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

_SPECIAL = {"NaN": math.nan, "+Inf": math.inf, "Inf": math.inf, "-Inf": -math.inf}
_ESCAPES = {"\\": "\\", '"': '"', "n": "\n"}


class ParseError(ValueError):
    def __init__(self, lineno: int, message: str) -> None:
        super().__init__(f"line {lineno}: {message}")
        self.lineno = lineno


@dataclass(frozen=True)
class Sample:
    name: str
    labels: dict[str, str]
    value: float
    timestamp_ms: int | None = None


@dataclass
class Exposition:
    samples: list[Sample] = field(default_factory=list)
    types: dict[str, str] = field(default_factory=dict)
    help: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def by_name(self, name: str) -> list[Sample]:
        return [s for s in self.samples if s.name == name]


def parse(text: str, *, strict: bool = False) -> Exposition:
    """Parse exposition text. Bad lines raise in ``strict`` mode, else go to ``errors``."""
    out = Exposition()
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        try:
            if line.startswith("#"):
                _parse_comment(line, out)
            else:
                out.samples.append(_parse_sample(line, lineno))
        except ParseError as e:
            if strict:
                raise
            out.errors.append(str(e))
    return out


def _parse_comment(line: str, out: Exposition) -> None:
    parts = line[1:].strip().split(None, 2)
    if len(parts) >= 3 and parts[0] == "TYPE":
        out.types[parts[1]] = parts[2].strip()
    elif len(parts) >= 2 and parts[0] == "HELP":
        out.help[parts[1]] = parts[2] if len(parts) == 3 else ""


def _parse_sample(line: str, lineno: int) -> Sample:
    i = 0
    n = len(line)
    while i < n and line[i] not in "{ \t":
        i += 1
    name = line[:i]
    if not name:
        raise ParseError(lineno, "missing metric name")
    labels: dict[str, str] = {}
    if i < n and line[i] == "{":
        i = _parse_labels(line, i + 1, labels, lineno)
    rest = line[i:].split()
    if not rest or len(rest) > 2:
        raise ParseError(lineno, "expected value and optional timestamp")
    value = _parse_float(rest[0], lineno)
    ts: int | None = None
    if len(rest) == 2:
        try:
            ts = int(rest[1])
        except ValueError as e:
            raise ParseError(lineno, f"bad timestamp {rest[1]!r}") from e
    return Sample(name, labels, value, ts)


def _parse_labels(line: str, i: int, labels: dict[str, str], lineno: int) -> int:
    """Parse ``k="v",...}`` starting at ``i``; return the index after ``}``."""
    n = len(line)
    while True:
        while i < n and line[i] in " \t,":
            i += 1
        if i >= n:
            raise ParseError(lineno, "unterminated label set")
        if line[i] == "}":
            return i + 1
        eq = line.find("=", i)
        if eq < 0:
            raise ParseError(lineno, "label without '='")
        key = line[i:eq].strip()
        i = eq + 1
        if i >= n or line[i] != '"':
            raise ParseError(lineno, f"label {key!r} value not quoted")
        i += 1
        buf: list[str] = []
        while True:
            if i >= n:
                raise ParseError(lineno, "unterminated label value")
            c = line[i]
            if c == "\\" and i + 1 < n:
                buf.append(_ESCAPES.get(line[i + 1], "\\" + line[i + 1]))
                i += 2
            elif c == '"':
                i += 1
                break
            else:
                buf.append(c)
                i += 1
        labels[key] = "".join(buf)


def _parse_float(s: str, lineno: int) -> float:
    if s in _SPECIAL:
        return _SPECIAL[s]
    try:
        return float(s)
    except ValueError as e:
        raise ParseError(lineno, f"bad value {s!r}") from e
