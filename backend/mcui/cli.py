"""Command-line entry point: ``mcui``."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence

from mcui import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mcui", description="microcloud-ui")
    parser.add_argument("--version", action="version", version=f"mcui {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the web server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true", help="auto-reload (development)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(COMMANDS[args.command](args))


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("mcui.main:app", host=args.host, port=args.port, reload=args.reload)
    return 0


COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {"serve": _serve}
