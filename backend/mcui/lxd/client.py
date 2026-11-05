"""Thin async client for the LXD REST API with endpoint failover (CF-2)."""

from __future__ import annotations

import ssl
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx

from mcui.lxd.tls import make_ssl_context


class LXDError(Exception):
    """LXD answered with an error, or could not be reached."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class LXDUnavailable(LXDError):
    """No endpoint could be reached."""


class LXDClient:
    """Talks to any of ``endpoints``, sticking to the last one that answered."""

    def __init__(
        self,
        endpoints: Sequence[str],
        *,
        verify: ssl.SSLContext,
        server_name: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        if not endpoints:
            raise ValueError("at least one endpoint is required")
        self.endpoints = [e.rstrip("/") for e in endpoints]
        self.server_name = server_name
        self._current = 0
        self._http = httpx.AsyncClient(verify=verify, timeout=timeout)

    @classmethod
    def from_files(
        cls,
        endpoints: Sequence[str],
        *,
        server_cert: Path,
        client_cert: Path,
        client_key: Path,
        server_name: str | None = None,
        timeout: float = 10.0,
    ) -> LXDClient:
        ctx = make_ssl_context(server_cert, client_cert, client_key)
        return cls(endpoints, verify=ctx, server_name=server_name, timeout=timeout)

    @property
    def endpoint(self) -> str:
        """The endpoint currently in use."""
        return self.endpoints[self._current]

    def _extensions(self) -> dict[str, Any]:
        return {"sni_hostname": self.server_name} if self.server_name else {}

    async def _send(
        self, method: str, url: str, params: dict[str, Any] | None, json: Any
    ) -> httpx.Response:
        return await self._http.request(
            method, url, params=params, json=json, extensions=self._extensions()
        )

    async def raw(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        base: str | None = None,
    ) -> httpx.Response:
        """Send a request. With ``base`` only that server is tried, else failover."""
        if base is not None:
            try:
                return await self._send(method, base.rstrip("/") + path, params, json)
            except httpx.TransportError as e:
                raise LXDUnavailable(_describe(e)) from e
        errors: list[str] = []
        n = len(self.endpoints)
        for i in range(n):
            idx = (self._current + i) % n
            ep = self.endpoints[idx]
            try:
                r = await self._send(method, ep + path, params, json)
            except httpx.TransportError as e:
                errors.append(f"{ep}: {_describe(e)}")
                continue
            self._current = idx
            return r
        raise LXDUnavailable("no LXD endpoint reachable: " + "; ".join(errors))

    async def query(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        base: str | None = None,
    ) -> Any:
        """Send a request and return the response ``metadata``; raise on LXD errors."""
        r = await self.raw(method, path, params=params, json=json, base=base)
        try:
            body = r.json()
        except ValueError as e:
            raise LXDError(f"non-JSON response (HTTP {r.status_code})", r.status_code) from e
        if not isinstance(body, dict):
            raise LXDError(f"unexpected response (HTTP {r.status_code})", r.status_code)
        if body.get("type") == "error" or r.status_code >= 400:
            msg = body.get("error") or f"HTTP {r.status_code}"
            raise LXDError(str(msg), int(body.get("error_code") or r.status_code))
        return body.get("metadata")

    async def get(self, path: str, **params: Any) -> Any:
        return await self.query("GET", path, params=params or None)

    async def aclose(self) -> None:
        await self._http.aclose()


def _describe(e: httpx.TransportError) -> str:
    text = str(e)
    return f"{type(e).__name__}: {text}" if text else type(e).__name__
