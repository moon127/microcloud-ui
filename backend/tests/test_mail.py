"""Alerts by email (D95): the smtp kind, against a small SMTP server of our own."""

import asyncio
import base64
import contextlib
import email
import email.policy
import ipaddress
import shutil
import socket
import socketserver
import ssl
import subprocess
import threading
import time

import pytest
import respx

from mcui.notify import kinds, mail, net
from mcui.notify.kinds import NotifyError, check
from tests.conftest import EP1, lxd_ok
from tests.test_api import CSRF_HEADER, client, login
from tests.test_notify import Clock, alert, app, public_dns, svc  # noqa: F401

pytestmark = pytest.mark.skipif(shutil.which("openssl") is None, reason="needs openssl")


@pytest.fixture(scope="module")
def cert(tmp_path_factory):
    """A self-signed certificate for localhost and mail.test, which the client trusts."""
    d = tmp_path_factory.mktemp("cert")
    subprocess.run(  # noqa: S603
        [  # noqa: S607
            "openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
            "-nodes", "-keyout", d / "key.pem", "-out", d / "cert.pem", "-days", "2",
            "-subj", "/CN=mail.test",
            "-addext", "subjectAltName=DNS:localhost,DNS:mail.test",
            "-addext", "keyUsage=critical,digitalSignature,keyCertSign",
        ],
        check=True,
        capture_output=True,
    )  # fmt: skip
    return d / "cert.pem", d / "key.pem"


class Smtp:
    """What the fake server saw, and how it behaves."""

    def __init__(
        self, cert, implicit=False, starttls=True, silent=False, flood=False, hangup=False
    ):
        self.cert, self.implicit, self.starttls, self.silent = cert, implicit, starttls, silent
        self.flood, self.hangup = flood, hangup
        self.mail: list[dict] = []
        self.helo: list[str] = []
        self.auth: list[tuple[str, str]] = []
        self.tls_used = False


class Handler(socketserver.StreamRequestHandler):
    timeout = 5

    def setup(self):
        s: Smtp = self.server.smtp  # type: ignore[attr-defined]
        self.s = s
        if s.implicit and not s.silent:  # silent: the client waits in its TLS handshake
            self.request = self._tls(self.request)
        super().setup()

    def _tls(self, sock):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(*self.s.cert)
        self.s.tls_used = True
        return ctx.wrap_socket(sock, server_side=True)

    def say(self, line):
        self.wfile.write(line.encode() + b"\r\n")
        self.wfile.flush()

    def handle(self):
        s = self.s
        if s.silent:
            time.sleep(3)
            return
        self.say("220 fake ESMTP")
        mail_from, rcpt, secure = None, [], s.implicit
        while line := self.rfile.readline():
            cmd = line.decode().strip()
            verb = cmd.split(" ")[0].upper()
            if verb == "EHLO" and s.flood:
                with contextlib.suppress(OSError):
                    for _ in range(10_000):
                        self.wfile.write(b"250-" + b"x" * 100 + b"\r\n")
                return
            if verb == "EHLO":
                s.helo.append(cmd[5:])
                ext = ["250-fake"]
                if s.starttls and not secure:
                    ext.append("250-STARTTLS")
                if secure:
                    ext.append("250-AUTH PLAIN")
                self.say("\r\n".join([*ext, "250 8BITMIME"]))
            elif verb == "STARTTLS":
                self.say("220 go ahead")
                self.request = self._tls(self.request)
                self.rfile = self.request.makefile("rb")
                self.wfile = self.request.makefile("wb")
                secure = True
            elif verb == "AUTH":
                parts = base64.b64decode(cmd.split(" ")[2]).split(b"\0")
                s.auth.append((parts[1].decode(), parts[2].decode()))
                if parts[2] == b"right":
                    self.say("235 ok")
                else:
                    # some servers quote what they were sent
                    self.say(f"535 5.7.8 bad credentials {cmd.split(' ')[2]}")
            elif verb == "MAIL":
                mail_from, rcpt = cmd[10:].strip("<>"), []
                self.say("250 ok")
            elif verb == "RCPT":
                to = cmd[8:].strip("<>")
                if to.startswith("bad"):
                    self.say("550 no such user")
                else:
                    rcpt.append(to)
                    self.say("250 ok")
            elif verb == "DATA":
                self.say("354 go")
                data = b""
                while (chunk := self.rfile.readline()) != b".\r\n":
                    data += chunk
                s.mail.append({"from": mail_from, "to": rcpt, "data": data})
                self.say("250 queued")
                if s.hangup:
                    return  # no answer to QUIT
            elif verb == "QUIT":
                self.say("221 bye")
                return
            else:
                self.say("502 what")


@pytest.fixture
def smtp_server(cert, monkeypatch):
    """Start a fake server: ``start(**behaviour)`` gives its state and port."""
    servers = []

    def trusting():
        ctx = ssl.create_default_context(cafile=str(cert[0]))
        return ctx

    monkeypatch.setattr(mail, "tls_context", trusting)

    def start(**kw):
        srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        srv.daemon_threads = True
        srv.smtp = Smtp(cert, **kw)  # type: ignore[attr-defined]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return srv.smtp, srv.server_address[1]

    yield start
    for srv in servers:
        srv.shutdown()
        srv.server_close()


def cfg(port, **over):
    return {
        "host": "localhost",
        "port": str(port),
        "security": "starttls",
        "sender": "alerts@example.org",
        "to": "ops@example.org, oncall@example.org",
        **over,
    }


EV = [{"event": "fired", "alert": alert(title="Disk <full>", detail="osd.1 & more")}]


def parsed(m):
    return email.message_from_bytes(
        m["data"].replace(b"\r\n..", b"\r\n."), policy=email.policy.default
    )


# --- settings ---------------------------------------------------------------------------


def test_check_settings():
    good = cfg(587, username="u")
    check("smtp", good, {"password": "p"}, cluster=False)
    # without a sign-in only a cluster channel (D95 review)
    check("smtp", cfg(587), {}, cluster=True)
    with pytest.raises(ValueError, match="needs a username and password"):
        check("smtp", cfg(587), {}, cluster=False)
    check(
        "smtp",
        {**cfg(587), "host": "2001:db8::25", "security": "none", "port": "25"},
        {},
        cluster=True,
    )
    bad = [
        ({"host": "mail.example.org:25"}, {}, "host"),
        ({"host": "-x.example.org"}, {}, "host"),
        ({"port": "0"}, {}, "port"),
        ({"port": "smtp"}, {}, "port"),
        ({"port": "٥٨٧"}, {}, "port"),  # digits, but not ASCII ones
        ({"security": "ssl"}, {}, "security"),
        ({"security": "none"}, {}, "a project's email channel"),
        ({}, {"password": ""}, "a username needs a password"),
        ({"sender": "Alerts <a@example.org>"}, {}, "sender"),
        ({"to": ""}, {}, "to is required"),
        ({"to": "a@example.org\r\nBcc: x@example.org"}, {}, "to:"),
        ({"to": ",".join(f"u{i}@example.org" for i in range(11))}, {}, "to:"),
    ]
    for over, secrets, why in bad:
        with pytest.raises(ValueError, match=why):
            check("smtp", {**good, **over}, secrets or {"password": "p"}, cluster=False)
    with pytest.raises(ValueError, match="a username needs a password"):
        check("smtp", cfg(587), {"password": "p"}, cluster=True)
    # no password in the clear, even for a cluster admin's relay
    with pytest.raises(ValueError, match="need STARTTLS or TLS"):
        check("smtp", {**good, "security": "none"}, {"password": "p"}, cluster=True)
    assert kinds.targets("smtp", cfg(587), {}) == ["smtp://localhost:587"]
    assert mail.target({"host": "2001:db8::25", "port": "25"}) == "smtp://[2001:db8::25]:25"


def test_message():
    two = [*EV, {"event": "resolved", "alert": alert(2, resolved_at=Clock.t)}]
    msg = mail.message(cfg(587), two, "https://mcui.example.org", "mcui.example.org")
    assert msg["Subject"] == "⚠️ 2 alert changes"
    assert msg["To"] == "ops@example.org, oncall@example.org"
    assert msg["Auto-Submitted"] == "auto-generated"
    assert msg["Message-ID"].endswith("@mcui.example.org>")
    text = msg.get_body(("plain",)).get_content()
    page = msg.get_body(("html",)).get_content()
    assert "WARNING: Disk <full>" in text and "https://mcui.example.org/x" in text
    assert "Disk &lt;full&gt;" in page and "osd.1 &amp; more" in page and "<full>" not in page
    assert "Resolved: t" in page and 'href="https://mcui.example.org/alerts"' in page
    # a title can't add headers
    bad = [{"event": "fired", "alert": alert(title="x\r\nBcc: evil@example.org")}]
    msg = mail.message(cfg(587), bad, None, "h")
    assert "\n" not in msg["Subject"] and "Bcc" not in msg
    many = [{"event": "fired", "alert": alert(i)} for i in range(12)]
    assert "…and 2 more" in mail.text(many, None) and "…and 2 more" in mail.page(many, None)


# --- sending ----------------------------------------------------------------------------


async def test_starttls_and_login(smtp_server):
    s, port = smtp_server()
    await mail.send(
        cfg(port, username="alerts"), {"password": "right"}, EV, "https://mcui.example.org"
    )
    assert s.tls_used and s.auth == [("alerts", "right")]
    assert s.helo[-1] == "mcui.example.org"
    [m] = s.mail
    assert m["from"] == "alerts@example.org" and m["to"] == [
        "ops@example.org",
        "oncall@example.org",
    ]
    assert parsed(m)["Subject"] == "⚠️ WARNING: Disk <full>"
    with pytest.raises(NotifyError, match=r"SMTP 535: 5.7.8 bad credentials \*\*\*$"):
        await mail.send(cfg(port, username="alerts"), {"password": "wrong"}, EV, None)


async def test_implicit_tls(smtp_server):
    s, port = smtp_server(implicit=True)
    await mail.send(cfg(port, security="tls"), {}, EV, None)
    assert s.tls_used and len(s.mail) == 1


async def test_plain_relay(smtp_server):
    s, port = smtp_server(starttls=False)
    await mail.send(cfg(port, security="none"), {}, EV, None)
    assert not s.tls_used and len(s.mail) == 1
    # asked for STARTTLS, not offered: it fails rather than send in the clear
    with pytest.raises(NotifyError, match="STARTTLS"):
        await mail.send(cfg(port), {}, EV, None)
    assert len(s.mail) == 1


async def test_refusals(smtp_server):
    s, port = smtp_server()
    # one of two refused: the other has it, so it counts as sent (no duplicate on a retry)
    await mail.send(cfg(port, to="bad@example.org, ops@example.org"), {}, EV, None)
    assert s.mail[-1]["to"] == ["ops@example.org"]
    with pytest.raises(NotifyError, match="refused for bad@example.org"):
        await mail.send(cfg(port, to="bad@example.org"), {}, EV, None)


async def test_untrusted_certificate(smtp_server, monkeypatch):
    s, port = smtp_server()
    monkeypatch.setattr(mail, "tls_context", ssl.create_default_context)
    with pytest.raises(NotifyError, match="certificate isn't trusted"):
        await mail.send(cfg(port), {}, EV, None)
    assert s.mail == []


async def test_name_must_match_certificate(smtp_server):
    _, port = smtp_server()
    with pytest.raises(NotifyError, match="certificate isn't trusted"):
        await mail.send(cfg(port, host="127.0.0.1"), {}, EV, None)


async def test_out_of_time_stops_the_thread(smtp_server, monkeypatch):
    _, port = smtp_server(silent=True)
    ended = threading.Event()
    real = mail._send

    def watched(*a):
        try:
            real(*a)
        finally:
            ended.set()

    monkeypatch.setattr(mail, "_send", watched)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(mail.send(cfg(port), {}, EV, None), 0.3)
    # the worker thread ends with the shut socket, not after its 15 s read timeout (or
    # when the server hangs up, after 3 s)
    assert await asyncio.to_thread(ended.wait, 1.5)


async def test_stalled_tls_handshake_stops_too(smtp_server, monkeypatch):
    """Out of time before smtplib has a socket of its own: still stopped (D95 review)."""
    _, port = smtp_server(silent=True)
    ended = threading.Event()
    real = mail._send

    def watched(*a):
        try:
            real(*a)
        finally:
            ended.set()

    monkeypatch.setattr(mail, "_send", watched)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(mail.send(cfg(port, security="tls"), {}, EV, None), 0.3)
    assert await asyncio.to_thread(ended.wait, 1.5)


async def test_slow_servers_leave_the_shared_pool_alone(smtp_server):
    _, port = smtp_server(silent=True)
    sends = [
        asyncio.create_task(asyncio.wait_for(mail.send(cfg(port), {}, EV, None), 1))
        for _ in range(20)
    ]
    await asyncio.sleep(0.1)
    t0 = time.monotonic()
    assert await asyncio.to_thread(lambda: 1) == 1  # sign-ins, DNS
    assert time.monotonic() - t0 < 0.5
    for t in sends:
        with pytest.raises(TimeoutError):
            await t


async def test_endless_reply(smtp_server):
    _, port = smtp_server(flood=True)
    with pytest.raises(NotifyError, match="too long"):
        await mail.send(cfg(port), {}, EV, None)


async def test_hang_up_after_taking_it(smtp_server):
    s, port = smtp_server(hangup=True)
    await mail.send(cfg(port), {}, EV, None)  # taken: not sent again
    assert len(s.mail) == 1


async def test_own_name(smtp_server):
    s, port = smtp_server()
    await mail.send(cfg(port), {}, EV, "https://192.0.2.10:8443")
    await mail.send(cfg(port), {}, EV, "https://[2001:db8::10]")
    await mail.send(cfg(port), {}, EV, None)
    assert s.helo[0] == "[192.0.2.10]" and s.helo[2] == "[IPv6:2001:db8::10]"
    assert s.helo[4] == socket.getfqdn()
    # not the machine's name to a project's server; a name in ASCII
    await mail.send(cfg(port), {}, EV, None, pin="127.0.0.1")
    await mail.send(cfg(port), {}, EV, "https://bücher.example")
    assert s.helo[6] == "microcloud-ui.invalid" and s.helo[8] == "xn--bcher-kva.example"
    assert parsed(s.mail[0])["Message-ID"].endswith("@192.0.2.10>")


async def test_no_server(smtp_server):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with pytest.raises(OSError):
        await mail.send(cfg(port), {}, EV, None)


# --- through the notifier ---------------------------------------------------------------


async def test_cluster_channel_sends(svc, smtp_server):  # noqa: F811
    s, port = smtp_server()
    n = svc.notifier
    ch = n.store.channel(
        n.store.add(
            {"name": "mail", "kind": "smtp", "project": None, "config": cfg(port), "secrets": {}},
            "admin",
        )
    )
    await n.test(ch)
    assert "Test notification" in parsed(s.mail[-1])["Subject"]
    assert n.store.deliveries(ch["id"])[0]["status"] == "sent"


@pytest.mark.parametrize("security", ["starttls", "tls"])
async def test_project_channel_connects_to_the_checked_address(
    svc,  # noqa: F811
    smtp_server,
    monkeypatch,
    security,
):
    """mail.test is checked (public), then the send connects to that very address, while
    TLS still checks the certificate against the name."""
    s, port = smtp_server(implicit=security == "tls")
    asked = []

    async def resolve(host, port_):
        asked.append(host)
        return ["127.0.0.1"] if host == "mail.test" else ["10.0.0.5"]

    monkeypatch.setattr(net, "resolve", resolve)
    monkeypatch.setattr(net, "is_public", lambda a: a == ipaddress.ip_address("127.0.0.1"))
    n = svc.notifier
    # checked as mail.test: the pin is found (D95 review)
    config = cfg(port, host="Mail.Test", security=security, username="pat")
    secrets = {"password": "right"}
    ch = n.store.channel(
        n.store.add(
            {"name": "m", "kind": "smtp", "project": "web", "config": config, "secrets": secrets},
            "pat",
        )
    )
    await n.test(ch)
    assert asked == ["mail.test"] and len(s.mail) == 1
    # a host without a pin isn't sent to
    with pytest.raises(NotifyError, match="wasn't checked"):
        await kinds.send(n.http, "smtp", config, secrets, EV, None, pins={})
    # a name that now leads inside is refused before any connection
    monkeypatch.setattr(net, "is_public", lambda a: False)
    with pytest.raises(NotifyError, match="isn't a public address"):
        await n.test(ch)
    assert len(s.mail) == 1


async def test_failures_are_readable(svc):  # noqa: F811
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    n = svc.notifier
    ch = n.store.channel(
        n.store.add(
            {"name": "x", "kind": "smtp", "project": None, "config": cfg(port), "secrets": {}},
            "admin",
        )
    )
    with pytest.raises(NotifyError, match="can't connect: .*refused"):
        await n.test(ch)


@respx.mock
async def test_api(app, svc):  # noqa: F811
    respx.get(f"{EP1}/1.0/projects/web").mock(
        return_value=lxd_ok({"name": "web", "config": {"restricted": "true"}})
    )
    async with client(app) as c:
        h = {CSRF_HEADER: await login(c, "admin", "adminpw")}
        kinds_ = (await c.get("/api/notify/kinds")).json()
        assert kinds_["smtp"]["choices"] == {"security": ["starttls", "tls", "none"]}
        assert kinds_["smtp"]["defaults"] == {"port": "587", "security": "starttls"}
        config = {k: v for k, v in cfg(587, username="u").items() if k not in ("port", "security")}
        body = {"name": "Ops mail", "kind": "smtp", "config": config, "secrets": {"password": "pw"}}
        r = await c.post("/api/notify/channels", json=body, headers=h)
        assert r.status_code == 201, r.text
        saved = r.json()
        assert saved["config"]["port"] == "587" and saved["secrets_set"] == ["password"]
        # another server: the password has to be typed again
        moved = {**body, "config": {**config, "host": "mail.example.net"}, "secrets": {}}
        r = await c.put(f"/api/notify/channels/{saved['id']}", json=moved, headers=h)
        assert r.status_code == 400 and "enter password again" in r.text
        h = {CSRF_HEADER: await login(c, "pat", "patpw")}
        plain = {**body, "config": {**config, "security": "none", "username": ""}, "secrets": {}}
        r = await c.post("/api/projects/web/notify/channels", json=plain, headers=h)
        assert r.status_code == 400 and "STARTTLS or TLS" in r.text


async def test_send_test_is_throttled(app, svc, smtp_server):  # noqa: F811
    s, port = smtp_server()
    n = svc.notifier
    cid = n.store.add(
        {"name": "m", "kind": "smtp", "project": None, "config": cfg(port), "secrets": {}}, "admin"
    )
    async with client(app) as c:
        h = {CSRF_HEADER: await login(c, "admin", "adminpw")}
        r = await c.post(f"/api/notify/channels/{cid}/test", headers=h)
        assert r.json() == {"ok": True}
        r = await c.post(f"/api/notify/channels/{cid}/test", headers=h)
        assert r.status_code == 429 and "wait 10 seconds" in r.text
        n.tested[cid] -= 11
        assert (await c.post(f"/api/notify/channels/{cid}/test", headers=h)).json()["ok"]
    assert len(s.mail) == 2
