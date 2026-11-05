"""LXDClient.websocket against a real TLS websocket server requiring our client cert."""

import ssl

import pytest
from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.http11 import Response

from mcui.lxd.client import LXDClient, LXDError, LXDUnavailable
from tests.conftest import SERVER_NAME


def server_ctx(certs):
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(certs.server_cert, certs.server_key)
    ctx.load_verify_locations(certs.client_cert)
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


def client(certs, url):
    return LXDClient.from_files(
        [url],
        server_cert=certs.server_cert,
        client_cert=certs.client_cert,
        client_key=certs.client_key,
        server_name=SERVER_NAME,
        timeout=5,
    )


async def test_connects_with_mtls_and_query(certs):
    seen = []

    async def handler(conn):
        seen.append(conn.request.path)
        await conn.send('{"type":"lifecycle"}')
        await conn.send(b"\x00bin")

    async with serve(handler, "127.0.0.1", 0, ssl=server_ctx(certs)) as srv:
        port = srv.sockets[0].getsockname()[1]
        c = client(certs, f"https://127.0.0.1:{port}")
        ws = await c.websocket(
            "/1.0/events", {"type": "operation,lifecycle", "all-projects": "true"}
        )
        assert await ws.recv() == '{"type":"lifecycle"}'
        assert await ws.recv() == b"\x00bin"
        await ws.close()
        await c.aclose()
    assert seen == ["/1.0/events?type=operation%2Clifecycle&all-projects=true"]


async def test_refused_handshake_is_lxd_error(certs):
    def reject(conn, request):
        return Response(403, "Forbidden", Headers(), b"no")

    async with serve(
        lambda c: None, "127.0.0.1", 0, ssl=server_ctx(certs), process_request=reject
    ) as srv:
        port = srv.sockets[0].getsockname()[1]
        c = client(certs, f"https://127.0.0.1:{port}")
        with pytest.raises(LXDError, match="HTTP 403") as e:
            await c.websocket("/1.0/events", base=f"https://127.0.0.1:{port}/")
        assert e.value.status == 403
        await c.aclose()


async def test_unreachable(certs):
    c = client(certs, "https://127.0.0.1:1")
    with pytest.raises(LXDUnavailable, match="no LXD endpoint reachable"):
        await c.websocket("/1.0/events")
    await c.aclose()
