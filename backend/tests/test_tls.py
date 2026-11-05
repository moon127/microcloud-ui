"""Real mTLS handshakes against an in-process server using an LXD-style cert (NF-3)."""

import asyncio
import json
import ssl

import pytest

from mcui.lxd.client import LXDClient, LXDUnavailable
from tests.conftest import SERVER_NAME


async def _serve(certs, client_ca):
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(certs.server_cert, certs.server_key)
    ctx.load_verify_locations(client_ca)
    ctx.verify_mode = ssl.CERT_REQUIRED
    seen: list[dict] = []

    async def handle(reader, writer):
        try:
            await reader.readuntil(b"\r\n\r\n")
        except (asyncio.IncompleteReadError, ConnectionError):  # handshake failed
            writer.close()
            return
        peer = writer.get_extra_info("peercert")
        seen.append(peer)
        body = json.dumps({"type": "sync", "metadata": {"auth": "trusted"}}).encode()
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
            + body
        )
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=ctx)
    port = server.sockets[0].getsockname()[1]
    return server, f"https://127.0.0.1:{port}", seen


def _client(certs, url, server_cert=None, server_name=SERVER_NAME):
    return LXDClient.from_files(
        [url],
        server_cert=server_cert or certs.server_cert,
        client_cert=certs.client_cert,
        client_key=certs.client_key,
        server_name=server_name,
        timeout=5,
    )


async def test_pinned_cert_with_server_name_override(certs):
    server, url, seen = await _serve(certs, certs.client_cert)
    async with server:
        c = _client(certs, url)
        assert await c.get("/1.0") == {"auth": "trusted"}
        await c.aclose()
    assert seen and seen[0]  # the server saw our client certificate


async def test_wrong_server_name_rejected(certs):
    server, url, _ = await _serve(certs, certs.client_cert)
    async with server:
        c = _client(certs, url, server_name="not-in-san")
        with pytest.raises(LXDUnavailable, match="ConnectError"):
            await c.get("/1.0")
        await c.aclose()


async def test_unpinned_cert_rejected(certs):
    server, url, _ = await _serve(certs, certs.client_cert)
    async with server:
        c = _client(certs, url, server_cert=certs.other_cert)
        with pytest.raises(LXDUnavailable, match="CERTIFICATE_VERIFY_FAILED"):
            await c.get("/1.0")
        await c.aclose()


async def test_untrusted_client_cert_rejected_by_server(certs):
    server, url, seen = await _serve(certs, certs.other_cert)
    async with server:
        c = _client(certs, url)
        with pytest.raises(LXDUnavailable):
            await c.get("/1.0")
        await c.aclose()
    assert seen == []
