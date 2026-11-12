"""Talk to the test cluster's LXD API with the dev config's certificates (dev aid, untested).

There is no `lxc` on the owner's Mac, so live checks go through the API. Uses
~/.config/mcui/config.toml (or $MCUI_CONFIG): first endpoint, pinned cluster cert,
server_name, management client cert. Run with the repo venv:

    .venv/bin/python scripts/lxd_api.py GET "/1.0/instances?project=mcui-test"
    .venv/bin/python scripts/lxd_api.py PATCH /1.0/projects/mcui-test '{"config": {...}}'
    .venv/bin/python scripts/lxd_api.py exec mcui-test-vm "uname -a"
    .venv/bin/python scripts/lxd_api.py push mcui-test-vm dist/x.tar.gz /root/x.tar.gz

Or import it: ``from lxd_api import req, wait, exec_`` (with scripts/ on sys.path).
Mutations belong in project mcui-test only (agents.md D41, CLAUDE.md).
"""

from __future__ import annotations

import json
import os
import ssl
import sys
from pathlib import Path
from typing import Any

import httpx

from mcui.config import load_settings

PROJECT = "mcui-test"
_cfg = load_settings(os.environ.get("MCUI_CONFIG") or Path.home() / ".config/mcui/config.toml")
if _cfg.lxd is None:
    raise SystemExit("no [lxd] in the config")
_ctx = ssl.create_default_context(cafile=str(_cfg.lxd.server_cert))
_ctx.load_cert_chain(str(_cfg.lxd.client_cert), str(_cfg.lxd.client_key))
_sni = {"sni_hostname": _cfg.lxd.server_name} if _cfg.lxd.server_name else {}
_client = httpx.Client(base_url=_cfg.lxd.endpoints[0], verify=_ctx, timeout=900)


def req(method: str, path: str, **kw: Any) -> Any:
    """One API call; JSON replies are returned parsed, LXD errors exit."""
    r = _client.request(method, path, extensions=_sni, **kw)
    if r.headers.get("content-type", "").startswith("application/json"):
        j = r.json()
        if j.get("type") == "error":
            raise SystemExit(f"{method} {path}: {j.get('error')}")
        return j
    r.raise_for_status()
    return r


def wait(j: Any) -> Any:
    """Wait for an async operation; exit if it failed."""
    if j.get("type") != "async":
        return j
    w = req("GET", f"{j['operation']}/wait?timeout=900")
    if w["metadata"]["status"] != "Success":
        raise SystemExit(f"operation failed: {w['metadata'].get('err')}")
    return w


def exec_(inst: str, cmd: str, project: str = PROJECT, env: dict[str, str] | None = None):
    """Run ``bash -lc cmd`` in an instance (needs lxd-agent in a VM): (rc, stdout, stderr)."""
    j = req(
        "POST",
        f"/1.0/instances/{inst}/exec?project={project}",
        json={
            "command": ["bash", "-lc", cmd],
            "record-output": True,
            "interactive": False,
            "wait-for-websocket": False,
            "environment": env or {},
        },
    )
    md = wait(j)["metadata"]["metadata"]
    out = {fd: req("GET", f"{p}?project={project}").text for fd, p in md.get("output", {}).items()}
    return md.get("return"), out.get("1", ""), out.get("2", "")


def push(inst: str, local: str, remote: str, project: str = PROJECT) -> None:
    req(
        "POST",
        f"/1.0/instances/{inst}/files?path={remote}&project={project}",
        content=Path(local).read_bytes(),
        headers={"X-LXD-type": "file", "X-LXD-mode": "0644", "X-LXD-uid": "0", "X-LXD-gid": "0"},
    )


def main(argv: list[str]) -> int:
    if argv[:1] == ["exec"]:
        rc, out, err = exec_(argv[1], argv[2])
        sys.stdout.write(out)
        sys.stderr.write(err)
        return int(rc or 0)
    if argv[:1] == ["push"]:
        push(*argv[1:4])
        return 0
    method, path, *body = argv
    j = wait(req(method, path, json=json.loads(body[0]) if body else None))
    print(json.dumps(j.get("metadata", j), indent=2) if isinstance(j, dict) else j.text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
