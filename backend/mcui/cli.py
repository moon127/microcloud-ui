"""Command-line entry point: ``mcui``."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

from mcui import __version__
from mcui.config import (
    CONFIG_ENV,
    DEFAULT_CONFIG,
    ConfigError,
    Settings,
    config_path,
    load_settings,
)
from mcui.lxd.client import LXDClient

ClientsFn = Callable[[Settings, LXDClient, LXDClient], Awaitable[int]]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mcui", description="microcloud-ui")
    parser.add_argument("--version", action="version", version=f"mcui {__version__}")
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        help=f"config.toml (default: ${CONFIG_ENV}, else {DEFAULT_CONFIG} if it exists)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the web server")
    serve.add_argument("--host", help="listen address (default: [server] host, 127.0.0.1)")
    serve.add_argument("--port", type=int, help="listen port (default: [server] port, 8000)")
    serve.add_argument("--reload", action="store_true", help="auto-reload (development)")

    sub.add_parser("check", help="check LXD connectivity, certificates, metrics and features")

    rec = sub.add_parser("record-fixtures", help="capture scrubbed LXD responses for tests")
    rec.add_argument("--out", type=Path, default=Path("backend/tests/fixtures/recorded"))

    user = sub.add_parser("user", help="manage local UI users")
    usub = user.add_subparsers(dest="user_command", required=True)
    add = usub.add_parser("add", help="create a user")
    add.add_argument("username")
    add.add_argument("--role", choices=["admin", "viewer"], default="viewer")
    add.add_argument("--password-stdin", action="store_true", help="read password from stdin")
    pw = usub.add_parser("passwd", help="set a user's password (logs them out)")
    pw.add_argument("username")
    pw.add_argument("--password-stdin", action="store_true")
    usub.add_parser("list", help="list users")
    rm = usub.add_parser("delete", help="delete a user")
    rm.add_argument("username")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(COMMANDS[args.command](args))
    except ConfigError as e:
        print(f"mcui: config error: {e}", file=sys.stderr)
        return 2


def _settings(args: argparse.Namespace) -> Settings:
    return load_settings(args.config)


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    if args.config is not None:
        os.environ[CONFIG_ENV] = str(args.config)
    server = _settings(args).server  # fail fast with a readable error
    used = config_path(args.config)
    print(
        f"mcui: config {used}"
        if used
        else "mcui: no config file; built-in defaults (no cluster, data in ./data)",
        file=sys.stderr,
    )
    proxy = (
        {"forwarded_allow_ips": server.forwarded_allow_ips} if server.forwarded_allow_ips else {}
    )
    tls: dict[str, str] = {}
    if server.tls_cert is not None and server.tls_key is not None:
        tls = {"ssl_certfile": str(server.tls_cert), "ssl_keyfile": str(server.tls_key)}
    uvicorn.run(
        "mcui.main:create_app",
        factory=True,
        host=args.host or server.host,
        port=args.port or server.port,
        reload=args.reload,
        # one process: the collector and live metrics live in it
        workers=1,
        server_header=False,
        **proxy,  # type: ignore[arg-type]
        **tls,  # type: ignore[arg-type]
    )
    return 0


def _with_clients(args: argparse.Namespace, fn: ClientsFn) -> int:
    from mcui.services import lxd_clients

    settings = _settings(args)
    clients = lxd_clients(settings)
    if clients is None:
        print("mcui: no [lxd] section in the config", file=sys.stderr)
        return 2
    api, metrics = clients

    async def go() -> int:
        try:
            return await fn(settings, api, metrics)
        finally:
            await api.aclose()
            await metrics.aclose()

    return asyncio.run(go())


def _check(args: argparse.Namespace) -> int:
    from mcui.check import run_check

    async def fn(settings: Settings, api: LXDClient, metrics: LXDClient) -> int:
        ok = await run_check(settings, api, metrics, print)
        return 0 if ok else 1

    return _with_clients(args, fn)


def _record(args: argparse.Namespace) -> int:
    from mcui.record import record

    async def fn(settings: Settings, api: LXDClient, metrics: LXDClient) -> int:
        failures = await record(settings, api, metrics, args.out, print)
        return 1 if failures else 0

    return _with_clients(args, fn)


def _read_password(args: argparse.Namespace) -> str:
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\n")
    first = getpass.getpass("Password: ")
    if first != getpass.getpass("Repeat password: "):
        raise SystemExit("mcui: passwords do not match")
    return first


def _user(args: argparse.Namespace) -> int:
    from mcui.auth.store import AuthError, Store

    store = Store.open(_settings(args).server.data_dir)
    try:
        cmd = args.user_command
        if cmd == "list":
            for u in store.list_users():
                print(f"{u.username}\t{u.role}")
        elif cmd == "add":
            store.add_user(args.username, _read_password(args), args.role)
            store.audit(
                None, "user.create", args.username, detail={"role": args.role, "via": "cli"}
            )
            print(f"created {args.role} {args.username}")
        elif cmd == "passwd":
            store.set_password(args.username, _read_password(args))
            store.audit(None, "user.update", args.username, detail={"password": True, "via": "cli"})
            print(f"password changed for {args.username}")
        else:
            store.delete_user(args.username)
            store.audit(None, "user.delete", args.username, detail={"via": "cli"})
            print(f"deleted {args.username}")
    except AuthError as e:
        print(f"mcui: {e}", file=sys.stderr)
        return 1
    finally:
        store.close()
    return 0


COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "serve": _serve,
    "check": _check,
    "record-fixtures": _record,
    "user": _user,
}
