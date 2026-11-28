"""Signing in to a mail server with OAuth2 (D96): the ``smtp`` kind's ``auth`` setting.

- ``microsoft``: Microsoft 365 with an app registration's client credentials (no browser):
  a token for ``https://outlook.office365.com/.default`` from the tenant's endpoint.
- ``google``: Google (Gmail, Workspace): an admin signs in once through the app (the
  authorization code flow with PKCE), and the refresh token is kept as the channel's
  secret; each send swaps it for an access token.
- ``oauth2``: any token endpoint that gives client credentials a token.

The token goes to the server as SASL XOAUTH2. Access tokens are kept in memory until a
minute before they expire.

Sign-in: Google sends the browser back to ``/oauth/callback``, a page of the app. The
session cookie is SameSite=strict, so it doesn't come with that redirect; the page then
finishes the sign-in with an ordinary request, as the signed-in user, who must be the
one who started it (so nobody can be sent a sign-in link that would put their mailbox
behind someone else's channel).
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets as _secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from mcui.notify import kinds

AUTH = ("password", "microsoft", "google", "oauth2")
MS_TOKEN = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"  # noqa: S105 - a URL
MS_SCOPE = "https://outlook.office365.com/.default"
GOOGLE_AUTHORIZE = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"  # noqa: S105 - a URL
GOOGLE_SCOPE = "https://mail.google.com/"
# a directory (tenant) ID, or one of its domains
TENANT = re.compile(r"[0-9A-Fa-f-]{36}|[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?")
CLIENT_ID = re.compile(r"[\x21-\x7e]{1,256}")
SCOPE = re.compile(r"[\x21-\x7e]{1,256}( [\x21-\x7e]{1,256}){0,9}")
TOKEN_REPLY = 64 * 1024
HOSTS = {"microsoft": "smtp.office365.com", "google": "smtp.gmail.com"}
PENDING_PER_USER = 5
SIGN_IN_SECONDS = 600
EARLY = 60  # a token is renewed this long before it expires

# fields only some sign-ins use: field -> the auth values that show it (for the form)
WHEN = {
    "password": ("password",),
    "tenant": ("microsoft",),
    "client_id": ("microsoft", "google", "oauth2"),
    "client_secret": ("microsoft", "google", "oauth2"),
    "token_url": ("oauth2",),
    "scope": ("oauth2",),
}


def oauth(config: dict[str, str]) -> bool:
    return config.get("auth", "password") != "password"


def token_url(config: dict[str, str]) -> str | None:
    auth = config.get("auth", "password")
    if auth == "microsoft":
        return MS_TOKEN.format(tenant=config.get("tenant", ""))
    if auth == "google":
        return GOOGLE_TOKEN
    if auth == "oauth2":
        return config.get("token_url", "")
    return None


def check(config: dict[str, str], secrets: dict[str, str]) -> None:
    """The sign-in settings (the server's are ``mail.check``'s)."""
    auth = config.get("auth", "password")
    if auth not in AUTH:
        raise ValueError("auth: password, microsoft, google or oauth2")
    unused = [f for f, shown in WHEN.items() if auth not in shown and {**config, **secrets}.get(f)]
    if unused:
        raise ValueError(f"{', '.join(unused)}: not used with this sign-in")
    if auth == "password":
        return
    if config.get("security") == "none":
        raise ValueError("OAuth2 needs STARTTLS or TLS")
    host = config.get("host", "").lower().rstrip(".")
    if auth in HOSTS and host != HOSTS[auth]:
        # a token for someone's mailbox goes only to their provider's server, not to a
        # server whoever set up the channel picked (D96 review)
        raise ValueError(f"host: {HOSTS[auth]} for this sign-in")
    if not config.get("username"):
        raise ValueError("username: the mailbox to send as")
    if not CLIENT_ID.fullmatch(config.get("client_id", "")):
        raise ValueError("client_id is required")
    if not secrets.get("client_secret"):
        raise ValueError("client_secret is required")
    if auth == "microsoft" and not TENANT.fullmatch(config.get("tenant", "")):
        raise ValueError("tenant: the directory (tenant) ID, or its domain")
    if auth == "oauth2":
        url = httpx.URL(config.get("token_url", ""))
        if url.scheme != "https" or not url.host or url.userinfo:
            raise ValueError("token_url must be an https:// URL")
        if config.get("scope") and not SCOPE.fullmatch(config["scope"]):
            raise ValueError("scope: up to 10 scopes, separated by spaces")


# --- tokens -----------------------------------------------------------------------------

_tokens: dict[str, tuple[str, float]] = {}


def _key(config: dict[str, str], secrets: dict[str, str]) -> str:
    """What a cached token is for: a changed secret or setting means a new token."""
    parts = [token_url(config) or "", config.get("client_id", ""), config.get("scope", "")]
    parts += [secrets.get("client_secret", ""), secrets.get("refresh_token", "")]
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


async def _token_request(http: httpx.AsyncClient, url: str, form: dict[str, str]) -> dict[str, Any]:
    try:
        data = await kinds._post(http, url, TOKEN_REPLY, data=form)
    except kinds.NotifyError as e:
        raise kinds.NotifyError(f"sign-in: {_error(e)}") from e
    try:
        reply = json.loads(data)
        assert isinstance(reply, dict)  # noqa: S101 - turned into a NotifyError below
    except (ValueError, AssertionError) as e:
        raise kinds.NotifyError("sign-in: the token endpoint's answer wasn't readable") from e
    return reply


def _error(e: kinds.NotifyError) -> str:
    """A token endpoint's refusal, as its ``error`` and ``error_description`` say."""
    text = str(e)
    m = re.search(r'"error"\s*:\s*"([^"]{1,60})"', text)
    return f"{text.split(':')[0]}: {m.group(1)}" if m else text


async def access_token(
    http: httpx.AsyncClient, config: dict[str, str], secrets: dict[str, str], now: float
) -> str:
    key = _key(config, secrets)
    cached = _tokens.get(key)
    if cached and cached[1] > now + EARLY:
        return cached[0]
    auth = config["auth"]
    form = {"client_id": config["client_id"], "client_secret": secrets.get("client_secret", "")}
    if auth == "google":
        if not secrets.get("refresh_token"):
            raise kinds.NotifyError("not signed in yet: use Sign in on the channel")
        form |= {"grant_type": "refresh_token", "refresh_token": secrets["refresh_token"]}
    else:
        scope = MS_SCOPE if auth == "microsoft" else config.get("scope", "")
        form |= {"grant_type": "client_credentials"} | ({"scope": scope} if scope else {})
    reply = await _token_request(http, token_url(config) or "", form)
    token = reply.get("access_token")
    if not isinstance(token, str) or not token:
        raise kinds.NotifyError("sign-in: no access token in the answer")
    try:
        expires = float(reply.get("expires_in", 3600))
    except (TypeError, ValueError):
        expires = 3600.0
    if not 0 < expires <= 86400:  # inf, NaN, nonsense: an hour (D96 review)
        expires = 3600.0
    for k in [k for k, (_, until) in _tokens.items() if until <= now]:
        del _tokens[k]
    _tokens[key] = (token, now + expires)
    return token


def forget(config: dict[str, str], secrets: dict[str, str]) -> None:
    """Drop a cached token (the server refused it)."""
    _tokens.pop(_key(config, secrets), None)


def xoauth2(user: str, token: str) -> str:
    return f"user={user}\x01auth=Bearer {token}\x01\x01"


# --- signing in through the browser (Google) --------------------------------------------


@dataclass
class SignIn:
    channel_id: int
    username: str  # the app user who started it
    verifier: str
    redirect_uri: str
    expires: float
    settings: str = ""  # what it was started for (``settings``)


def settings(ch: dict[str, Any]) -> str:
    """What a sign-in is for: the app, the mailbox and the server; a change to any of
    them while it's under way means it isn't stored (D96 review)."""
    c = ch["config"]
    parts = [c.get(k, "") for k in ("auth", "client_id", "username", "host", "port")]
    parts.append(ch["secrets"].get("client_secret", ""))
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


class SignIns:
    """Sign-ins under way, by their ``state``; each can be finished once, by the user who
    started it, within ``SIGN_IN_SECONDS``."""

    def __init__(self) -> None:
        self.pending: dict[str, SignIn] = {}

    def start(
        self,
        ch: dict[str, Any],
        username: str,
        redirect_uri: str,
        now: float,
    ) -> str:
        """The URL to send the browser to."""
        self.pending = {k: v for k, v in self.pending.items() if v.expires > now}
        mine = [k for k, v in self.pending.items() if v.username == username]
        for k in mine[: max(len(mine) - PENDING_PER_USER + 1, 0)]:  # the oldest go
            del self.pending[k]
        state = _secrets.token_urlsafe(32)
        verifier = _secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        self.pending[state] = SignIn(
            ch["id"], username, verifier, redirect_uri, now + SIGN_IN_SECONDS, settings(ch)
        )
        params = {
            "client_id": ch["config"]["client_id"],
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": GOOGLE_SCOPE,
            # a refresh token, every time (Google gives one only on consent)
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
            "code_challenge": challenge.decode().rstrip("="),
            "code_challenge_method": "S256",
            "login_hint": ch["config"].get("username", ""),
        }
        return f"{GOOGLE_AUTHORIZE}?{urlencode(params)}"

    def take(self, state: str, username: str, now: float) -> SignIn:
        s = self.pending.get(state)
        if s is None or s.expires <= now:
            self.pending.pop(state, None)
            raise ValueError("this sign-in has expired or was already used: start it again")
        if s.username != username:
            # not consumed: the right user can still finish it
            raise PermissionError("this sign-in was started by another user")
        del self.pending[state]
        return s


async def finish(http: httpx.AsyncClient, ch: dict[str, Any], s: SignIn, code: str) -> str:
    """Swap the code for a refresh token."""
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": s.redirect_uri,
        "client_id": ch["config"]["client_id"],
        "client_secret": ch["secrets"].get("client_secret", ""),
        "code_verifier": s.verifier,
    }
    reply = await _token_request(http, GOOGLE_TOKEN, form)
    refresh = reply.get("refresh_token")
    if not isinstance(refresh, str) or not refresh:
        raise kinds.NotifyError("sign-in: Google gave no refresh token")
    if GOOGLE_SCOPE not in str(reply.get("scope", GOOGLE_SCOPE)).split():
        raise kinds.NotifyError("sign-in: mail access wasn't granted (tick it on Google's page)")
    return refresh
