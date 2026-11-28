"""Alerts by email (D95): the ``smtp`` channel kind.

Sent with the standard library's smtplib in a worker thread, so there's no new dependency.
Security is ``starttls`` (port 587), ``tls`` (465) or ``none`` (25, a relay on the LAN; no
password over it). The certificate is always checked against the server's name, and a
server that doesn't offer STARTTLS when it was asked for fails rather than sending in the
clear. A project's channel connects to the address it was checked at (``pin``), as the
HTTP kinds do (``net.PinnedTransport``).

Sends run in a pool of their own (``WORKERS``), so a slow server can't hold up sign-ins or
DNS in the app's shared pool. A send that runs out of time has its connection shut from
the event loop, so the thread stops too, at whatever stage, however slowly the server
answers; the server's replies are capped (``REPLY_BYTES``).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import functools
import html
import ipaddress
import logging
import re
import smtplib
import socket
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from typing import Any
from urllib.parse import urlsplit

from mcui.notify import kinds

SECURITY = ("starttls", "tls", "none")
PORTS = {"starttls": "587", "tls": "465", "none": "25"}
MAX_TO = 10
OP_SECONDS = 15.0  # each read or write
_LABEL = r"[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
HOST = re.compile(rf"(?=.{{1,253}}$){_LABEL}(\.{_LABEL})*")
# an address as it's typed in the form: no display name, comments or quoting
ADDRESS = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9.-]{1,253}")
log = logging.getLogger(__name__)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


def recipients(value: str) -> list[str]:
    return [a.strip() for a in value.split(",") if a.strip()]


def check(config: dict[str, str], secrets: dict[str, str], *, cluster: bool) -> None:
    host = config.get("host", "")
    if not (HOST.fullmatch(host) or _is_ip(host)):
        raise ValueError("host: a server name or address, without a port")
    port = config.get("port", "")
    if not (port.isascii() and port.isdigit() and 1 <= int(port) <= 65535):
        raise ValueError("port: a number from 1 to 65535")
    security = config.get("security", "")
    if security not in SECURITY:
        raise ValueError("security: starttls, tls or none")
    if security == "none" and not cluster:
        raise ValueError("a project's email channel must use STARTTLS or TLS")
    if not cluster and not config.get("username"):
        # a project can't use the cluster's address to send unsigned mail: straight to
        # someone's mail server, or through a relay that trusts it (D95 review)
        raise ValueError("a project's email channel needs a username and password")
    if security == "none" and (config.get("username") or secrets.get("password")):
        raise ValueError("a username and password need STARTTLS or TLS")
    if bool(config.get("username")) != bool(secrets.get("password")):
        raise ValueError("a username needs a password, and the other way round")
    if not ADDRESS.fullmatch(config.get("sender", "")):
        raise ValueError("sender: one email address, such as alerts@example.org")
    to = recipients(config.get("to", ""))
    if not to or len(to) > MAX_TO or not all(ADDRESS.fullmatch(a) for a in to):
        raise ValueError(f"to: 1 to {MAX_TO} email addresses, separated by commas")


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def target(config: dict[str, str]) -> str:
    """The server as a URL, for the address check and to see when it changes."""
    host = config.get("host", "")
    if ":" in host:
        host = f"[{host}]"
    return f"smtp://{host}:{config.get('port', '')}"


# --- writing ----------------------------------------------------------------------------


def subject(events: list[dict[str, Any]]) -> str:
    return kinds._cut(_CONTROL.sub(" ", kinds.summary(events)), 200)


def text(events: list[dict[str, Any]], app_url: str | None) -> str:
    parts = []
    for e in events[: kinds.MAX_EVENTS]:
        lines = [f"{kinds.label(e)}: {e['alert']['title']}", kinds._cut(kinds.body(e), 4000)]
        if url := kinds.link(e, app_url):
            lines.append(url)
        parts.append("\n".join(lines))
    if more := kinds._more(events):
        parts.append(more)
    parts.append("-- \nSent by microcloud-ui." + (f" Alerts: {app_url}/alerts" if app_url else ""))
    return "\n\n".join(parts)


def page(events: list[dict[str, Any]], app_url: str | None) -> str:
    """The HTML part: every bit of alert text escaped, links only to the app."""
    rows = []
    for e in events[: kinds.MAX_EVENTS]:
        title = html.escape(kinds._cut(f"{kinds.label(e)}: {e['alert']['title']}", 300))
        if url := kinds.link(e, app_url):
            title = f'<a href="{html.escape(url, quote=True)}">{title}</a>'
        colour = kinds._COLOUR[kinds._state(e)]
        rows.append(
            f'<div style="border-left:4px solid {colour};padding:4px 12px;margin:12px 0">'
            f"<strong>{title}</strong><br>{html.escape(kinds._cut(kinds.body(e), 4000))}</div>"
        )
    if more := kinds._more(events):
        rows.append(f"<p>{html.escape(more)}</p>")
    foot = "Sent by microcloud-ui."
    if app_url:
        foot += f' <a href="{html.escape(app_url, quote=True)}/alerts">Open alerts</a>'
    return (
        '<!doctype html><html><body style="font-family:sans-serif">'
        f"<p><strong>{html.escape(kinds.summary(events))}</strong></p>"
        f'{"".join(rows)}<p style="color:#666;font-size:small">{foot}</p></body></html>'
    )


def message(
    config: dict[str, str], events: list[dict[str, Any]], app_url: str | None, domain: str
) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject(events)
    msg["From"] = formataddr(("microcloud-ui", config["sender"]))
    msg["To"] = ", ".join(recipients(config["to"]))
    msg["Date"] = formatdate(time.time(), localtime=False)
    msg["Message-ID"] = make_msgid(domain=domain)
    # an automatic message: no out-of-office replies to it (RFC 3834)
    msg["Auto-Submitted"] = "auto-generated"
    msg.set_content(text(events, app_url))
    msg.add_alternative(page(events, app_url), subtype="html")
    return msg


# --- sending ----------------------------------------------------------------------------


def tls_context() -> ssl.SSLContext:
    """The system's trusted certificates (a seam for the tests' own CA)."""
    return ssl.create_default_context()


REPLY_BYTES = 64 * 1024  # all the server's replies in one session (D95 review)
WORKERS = 5  # emails being sent at once; the app's shared pool is left alone (D95 review)
_pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="mcui-smtp")


class _Capped:
    """The server's replies, read with a limit: smtplib reads ``250-`` lines for as long
    as they come, so a server could otherwise fill the app's memory."""

    def __init__(self, inner: Any) -> None:
        self.inner, self.read = inner, 0

    def readline(self, size: int = -1) -> bytes:
        line: bytes = self.inner.readline(size)
        self.read += len(line)
        if self.read > REPLY_BYTES:
            raise smtplib.SMTPResponseException(500, b"the server's reply is too long")
        return line

    def close(self) -> None:
        self.inner.close()


class _Session:
    """What the event loop needs to stop a send in its worker thread: a copy of the
    connection's socket, made before any TLS, which it can shut whatever stage the send
    is at (during the handshake too), without touching the TLS state in the thread."""

    def __init__(self) -> None:
        self.stopped = threading.Event()
        self.watch: socket.socket | None = None
        self.lock = threading.Lock()

    def started(self, sock: socket.socket) -> None:
        with self.lock:
            if self.stopped.is_set():
                sock.close()
                raise smtplib.SMTPServerDisconnected("out of time")
            self.watch = sock.dup()

    def stop(self) -> None:
        with self.lock:
            self.stopped.set()
            if self.watch is not None:
                with contextlib.suppress(OSError):
                    self.watch.shutdown(socket.SHUT_RDWR)

    def close(self) -> None:
        with self.lock:
            if self.watch is not None:
                self.watch.close()


class _SMTP(smtplib.SMTP):
    """Connects to ``pin`` (a project's channel: the checked address) while smtplib keeps
    the name for TLS (SNI and the certificate check use ``self._host``); TLS from the
    start when ``implicit``. Replies are capped."""

    def __init__(
        self, session: _Session, pin: str | None, implicit: ssl.SSLContext | None, **kw: Any
    ) -> None:
        self.session, self.pin, self.implicit = session, pin, implicit
        super().__init__(**kw)

    def _get_socket(self, host: str, port: int, timeout: float) -> socket.socket:
        # the name TLS checks (here and in starttls): Python 3.12 sets it only in
        # __init__, from where no host was given, so it's set here for every version
        self._host = host
        sock = socket.create_connection((self.pin or host, port), timeout)
        self.session.started(sock)
        if self.implicit is not None:
            return self.implicit.wrap_socket(sock, server_hostname=self._host)
        return sock

    def getreply(self) -> tuple[int, bytes]:
        if self.file is None and self.sock is not None:
            self.file = _Capped(self.sock.makefile("rb"))
        return super().getreply()


@functools.cache
def _own_name() -> str:
    return socket.getfqdn()  # it may ask DNS: once, in a worker


UNNAMED = "microcloud-ui.invalid"


async def _helo_name(app_url: str | None, project: bool) -> str:
    """The name the app gives itself (EHLO, and the Message-ID's domain): public_url's
    host; else the machine's own, but not to a project's server (it's a name from the
    cluster's DNS, D95 review). An address as a literal, ``[192.0.2.10]`` (RFC 5321); a
    name in ASCII (IDNA), as SMTP commands are."""
    name = urlsplit(app_url).hostname if app_url else None
    if not name:
        if project:
            return UNNAMED
        name = await asyncio.get_running_loop().run_in_executor(_pool, _own_name)
    if _is_ip(name):
        return f"[IPv6:{name}]" if ":" in name else f"[{name}]"
    try:
        return name.encode("idna").decode("ascii")
    except UnicodeError:
        return UNNAMED


def _send(
    config: dict[str, str],
    secrets: dict[str, str],
    msg: EmailMessage,
    helo: str,
    pin: str | None,
    session: _Session,
) -> None:
    security = config["security"]
    ctx = tls_context()
    smtp = _SMTP(
        session,
        pin,
        ctx if security == "tls" else None,
        local_hostname=helo,
        timeout=OP_SECONDS,
    )
    try:
        smtp.connect(config["host"], int(config["port"]))
        smtp.ehlo()
        if security == "starttls":
            smtp.starttls(context=ctx)  # fails if the server doesn't offer it
            smtp.ehlo()
        if config.get("username"):
            smtp.login(config["username"], secrets.get("password", ""))
        refused = smtp.send_message(msg, config["sender"], recipients(config["to"]))
        if refused:
            # the rest have it, so it isn't sent again (they'd get it twice)
            log.warning("notify: email refused for %s", ", ".join(sorted(refused)))
        # taken: a server that hangs up instead of answering QUIT doesn't make it a
        # failure, which would send it again (D95 review)
        with contextlib.suppress(smtplib.SMTPException, OSError):
            smtp.quit()
    finally:
        smtp.close()
        session.close()


def _hide(text: str, config: dict[str, str], secrets: dict[str, str]) -> str:
    """A server's reply without the password, nor the AUTH line that carries it."""
    password = secrets.get("password")
    if password:
        user = config.get("username", "")
        for form in (
            password,
            base64.b64encode(password.encode()).decode(),
            base64.b64encode(f"\0{user}\0{password}".encode()).decode(),
        ):
            text = text.replace(form, "***")
    return text


async def send(
    config: dict[str, str],
    secrets: dict[str, str],
    events: list[dict[str, Any]],
    app_url: str | None,
    pin: str | None = None,
) -> None:
    """Send one email; ``pin`` (a project's channel) is the address to connect to."""
    helo = await _helo_name(app_url, project=pin is not None)
    msg = message(config, events, app_url, helo.strip("[]").removeprefix("IPv6:"))
    session = _Session()
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(_pool, _send, config, secrets, msg, helo, pin, session)
    except asyncio.CancelledError:
        # out of time: the thread's read or write ends now, or it doesn't connect
        session.stop()
        raise
    except smtplib.SMTPResponseException as e:
        # the server's own words, on one line (a reply can quote what was sent)
        why = e.smtp_error
        why = why.decode("utf-8", "replace") if isinstance(why, bytes) else str(why)
        why = _hide(_CONTROL.sub(" ", why).strip(), config, secrets)
        raise kinds.NotifyError(f"SMTP {e.smtp_code}: {why}"[:300]) from e
    except smtplib.SMTPRecipientsRefused as e:
        raise kinds.NotifyError(f"refused for {', '.join(sorted(e.recipients))}") from e
    except ssl.SSLCertVerificationError as e:
        why = f"the server's certificate isn't trusted: {e.verify_message}"
        raise kinds.NotifyError(why) from e
    except smtplib.SMTPException as e:  # STARTTLS or AUTH not offered, a hang-up
        raise kinds.NotifyError(str(e)[:300] or type(e).__name__) from e
