"""mTLS context with the LXD server certificate pinned (D6, NF-3)."""

from __future__ import annotations

import ssl
from pathlib import Path


def make_ssl_context(server_cert: Path, client_cert: Path, client_key: Path) -> ssl.SSLContext:
    """Trust only ``server_cert`` and present the client cert/key.

    LXD's cluster.crt is a self-signed leaf (``CA:FALSE``); OpenSSL accepts it as
    a trust anchor when it is the only certificate loaded, so no system CAs are
    trusted. Hostname checking stays on; callers set the expected name with
    ``sni_hostname`` (``server_name`` in the config) because the SAN rarely
    lists member IPs. tests/test_tls.py covers this with real handshakes.
    """
    ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=str(server_cert))
    ctx.load_cert_chain(str(client_cert), str(client_key))
    return ctx
