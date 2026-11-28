"""Email with OAuth2 sign-in (D96): Microsoft 365, Google, any token endpoint."""

import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx

from mcui.notify import kinds, oauth
from mcui.notify.kinds import NotifyError, check
from tests.conftest import EP1, lxd_ok
from tests.test_api import CSRF_HEADER, client, login
from tests.test_mail import EV, cert, cfg, smtp_server  # noqa: F401
from tests.test_notify import Clock, app, public_dns, svc  # noqa: F401

MS = "https://login.microsoftonline.com/contoso.example/oauth2/v2.0/token"
GOOGLE = "https://oauth2.googleapis.com/token"
GENERIC = "https://id.example.org/token"


@pytest.fixture(autouse=True)
def no_cached_tokens():
    oauth._tokens.clear()
    yield
    oauth._tokens.clear()


def ms(port=587, **over):
    base = {"auth": "microsoft", "tenant": "contoso.example", "client_id": "app-1"}
    host = {"host": "smtp.office365.com"}
    return {**cfg(port, username="alerts@example.org"), **host, **base, **over}


def google(port=587, **over):
    base = {"auth": "google", "client_id": "g-1", "username": "alerts@example.org"}
    return {**cfg(port), "host": "smtp.gmail.com", **base, **over}


SECRET = {"client_secret": "cs-1"}


def token(value="good", expires=3600):
    return httpx.Response(200, json={"access_token": value, "expires_in": expires})


# --- settings ---------------------------------------------------------------------------


def test_check_settings():
    check("smtp", ms(), SECRET, cluster=False)  # signed in: fine for a project
    check("smtp", google(), SECRET, cluster=False)
    generic = cfg(587, auth="oauth2", client_id="c", token_url=GENERIC, username="a@example.org")
    check("smtp", {**generic, "scope": "smtp.send offline"}, SECRET, cluster=True)
    bad = [
        (ms(auth="kerberos"), SECRET, "auth:"),
        (ms(security="none"), SECRET, "needs STARTTLS or TLS"),
        (ms(username=""), SECRET, "username: the mailbox"),
        (ms(client_id=""), SECRET, "client_id is required"),
        (ms(), {}, "client_secret is required"),
        (ms(tenant="contoso example"), SECRET, "tenant:"),
        (ms(token_url=GENERIC), SECRET, "token_url: not used"),
        (ms(), {**SECRET, "password": "p"}, "password: not used"),
        (
            cfg(587, username="u"),
            {"password": "p", "client_secret": "x"},
            "client_secret: not used",
        ),
        ({**generic, "token_url": "http://id.example.org/t"}, SECRET, "https:// URL"),
        ({**generic, "token_url": "https://u:p@id.example.org/t"}, SECRET, "https:// URL"),
        ({**generic, "scope": "a\tb"}, SECRET, "scope:"),
    ]
    for config, secrets, why in bad:
        with pytest.raises(ValueError, match=why):
            check("smtp", config, secrets, cluster=True)
    # the token endpoint is a target: checked as public, and changing it needs the
    # secret again
    assert kinds.targets("smtp", ms(), SECRET) == ["smtp://smtp.office365.com:587", MS]
    # a provider's token goes only to the provider's own mail server (D96 review)
    with pytest.raises(ValueError, match="host: smtp.gmail.com for this sign-in"):
        check("smtp", google(host="mail.example.net"), SECRET, cluster=False)
    with pytest.raises(ValueError, match="host: smtp.office365.com"):
        check("smtp", ms(host="localhost"), SECRET, cluster=True)
    check("smtp", ms(host="SMTP.Office365.com"), SECRET, cluster=True)
    assert kinds.targets("smtp", generic, SECRET)[1] == GENERIC


# --- tokens -----------------------------------------------------------------------------


@respx.mock
async def test_tokens():
    http = httpx.AsyncClient()
    route = respx.post(MS).mock(return_value=token("t1", 3600))
    assert await oauth.access_token(http, ms(), SECRET, 1000) == "t1"
    form = parse_qs(route.calls.last.request.content.decode())
    assert form == {
        "client_id": ["app-1"],
        "client_secret": ["cs-1"],
        "grant_type": ["client_credentials"],
        "scope": ["https://outlook.office365.com/.default"],
    }
    # kept until a minute before it expires
    assert await oauth.access_token(http, ms(), SECRET, 1000 + 3500) == "t1"
    route.mock(return_value=token("t2"))
    assert await oauth.access_token(http, ms(), SECRET, 1000 + 3550) == "t2"
    # another secret: another token
    route.mock(return_value=token("t3"))
    assert await oauth.access_token(http, ms(), {"client_secret": "new"}, 1000) == "t3"
    oauth.forget(ms(), SECRET)
    route.mock(return_value=token("t4", "soon"))  # an odd expiry: an hour
    assert await oauth.access_token(http, ms(), SECRET, 1000) == "t4"
    # Google: from the refresh token
    g = respx.post(GOOGLE).mock(return_value=token("g1"))
    with pytest.raises(NotifyError, match="not signed in yet"):
        await oauth.access_token(http, google(), SECRET, 1000)
    signed = {**SECRET, "refresh_token": "rt-1"}
    assert await oauth.access_token(http, google(), signed, 1000) == "g1"
    form = parse_qs(g.calls.last.request.content.decode())
    assert form["grant_type"] == ["refresh_token"] and form["refresh_token"] == ["rt-1"]
    # any endpoint, with or without a scope
    gen = respx.post(GENERIC).mock(return_value=token("x1"))
    generic = cfg(587, auth="oauth2", client_id="c", token_url=GENERIC, username="a@example.org")
    await oauth.access_token(http, generic, SECRET, 1000)
    assert "scope" not in parse_qs(gen.calls.last.request.content.decode())
    oauth._tokens.clear()
    await oauth.access_token(http, {**generic, "scope": "smtp"}, SECRET, 1000)
    assert parse_qs(gen.calls.last.request.content.decode())["scope"] == ["smtp"]
    await http.aclose()


@respx.mock
async def test_token_failures():
    http = httpx.AsyncClient()
    route = respx.post(MS)
    route.mock(
        return_value=httpx.Response(
            401, json={"error": "invalid_client", "error_description": "AADSTS7000215: secret"}
        )
    )
    with pytest.raises(NotifyError, match="^sign-in: HTTP 401: invalid_client$"):
        await oauth.access_token(http, ms(), SECRET, 0)
    route.mock(return_value=httpx.Response(200, text="<html>"))
    with pytest.raises(NotifyError, match="wasn't readable"):
        await oauth.access_token(http, ms(), SECRET, 0)
    route.mock(return_value=httpx.Response(200, json=["x"]))
    with pytest.raises(NotifyError, match="wasn't readable"):
        await oauth.access_token(http, ms(), SECRET, 0)
    route.mock(return_value=httpx.Response(200, json={"token_type": "Bearer"}))
    with pytest.raises(NotifyError, match="no access token"):
        await oauth.access_token(http, ms(), SECRET, 0)
    route.mock(return_value=httpx.Response(500, text="down"))
    with pytest.raises(NotifyError, match="sign-in: HTTP 500: down"):
        await oauth.access_token(http, ms(), SECRET, 0)
    await http.aclose()


# --- sending ----------------------------------------------------------------------------


@respx.mock
async def test_send_with_xoauth2(smtp_server):  # noqa: F811
    s, port = smtp_server()
    route = respx.post(MS).mock(return_value=token("good"))
    http = httpx.AsyncClient()
    pins = {"smtp.office365.com": "127.0.0.1"}  # the cert names it too
    await kinds.send(http, "smtp", ms(port), SECRET, EV, None, pins=pins)
    assert s.auth == [("xoauth2", "user=alerts@example.org\x01auth=Bearer good\x01\x01")]
    assert len(s.mail) == 1
    # a refused token: the server's error, without the token, and the next send gets a
    # new one
    route.mock(return_value=token("stale-token-value"))
    oauth._tokens.clear()
    with pytest.raises(NotifyError, match=r"^SMTP 535: 5.7.8 bad token \*\*\*$") as e:
        await kinds.send(http, "smtp", ms(port), SECRET, EV, None, pins=pins)
    assert "stale" not in str(e.value)
    assert s.auth[-1] == ("reply", "")  # the challenge answered with an empty line
    route.mock(return_value=token("good"))
    await kinds.send(http, "smtp", ms(port), SECRET, EV, None, pins=pins)
    assert len(s.mail) == 2 and route.call_count == 3
    await http.aclose()


# --- signing in through Google ----------------------------------------------------------


def google_channel(svc, project=None, **over):  # noqa: F811
    n = svc.notifier
    return n.store.add(
        {
            "name": "gmail",
            "kind": "smtp",
            "project": project,
            "config": google(**over),
            "secrets": dict(SECRET),
        },
        "admin",
    )


@respx.mock
async def test_sign_in(app, svc):  # noqa: F811
    cid = google_channel(svc)
    n = svc.notifier
    async with client(app) as c:
        h = {CSRF_HEADER: await login(c, "admin", "adminpw")}
        info = (await c.get("/api/notify/oauth")).json()
        assert info == {"redirect_uri": "https://mcui.example.org/oauth/callback", "warning": None}
        r = await c.post(f"/api/notify/channels/{cid}/sign-in", headers=h)
        url = urlsplit(r.json()["url"])
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        assert url.netloc == "accounts.google.com"
        assert q["redirect_uri"] == "https://mcui.example.org/oauth/callback"
        assert q["scope"] == "https://mail.google.com/" and q["access_type"] == "offline"
        assert q["code_challenge_method"] == "S256" and q["login_hint"] == "alerts@example.org"
        state = q["state"]
        # another user can't finish it, nor can it be guessed
        h2 = {CSRF_HEADER: await login(c, "pat", "patpw")}
        r = await c.post("/api/notify/oauth/finish", json={"state": state, "code": "c"}, headers=h2)
        assert r.status_code == 403
        r = await c.post(
            "/api/notify/oauth/finish", json={"state": "x" * 40, "code": "c"}, headers=h2
        )
        assert r.status_code == 400 and "expired" in r.text
        h = {CSRF_HEADER: await login(c, "admin", "adminpw")}
        route = respx.post(GOOGLE).mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "a",
                    "refresh_token": "rt-1",
                    "scope": "https://mail.google.com/",
                    "expires_in": 3599,
                },
            )
        )
        r = await c.post("/api/notify/oauth/finish", json={"state": state, "code": "c"}, headers=h)
        assert r.json() == {"channel": "gmail", "path": "/settings#notifications"}
        form = parse_qs(route.calls.last.request.content.decode())
        assert form["code_verifier"][0] and form["grant_type"] == ["authorization_code"]
        assert form["redirect_uri"] == ["https://mcui.example.org/oauth/callback"]
        assert n.store.channel(cid)["secrets"]["refresh_token"] == "rt-1"  # noqa: S105
        listed = (await c.get("/api/notify/channels")).json()
        assert listed[0]["secrets_set"] == ["client_secret", "refresh_token"]
        # used once
        r = await c.post("/api/notify/oauth/finish", json={"state": state, "code": "c"}, headers=h)
        assert r.status_code == 400
        # saving it again keeps the sign-in; another mailbox or app drops it
        body = {
            "name": "gmail",
            "kind": "smtp",
            "config": dict(google()),
            "secrets": {},
        }
        r = await c.put(f"/api/notify/channels/{cid}", json=body, headers=h)
        assert r.status_code == 200, r.text
        assert "refresh_token" in r.json()["secrets_set"]
        body["config"]["username"] = "other@example.org"
        r = await c.put(f"/api/notify/channels/{cid}", json=body, headers=h)
        assert r.json()["secrets_set"] == ["client_secret"]
        # Google said no, or left mail access out
        n.tested.clear()
        for reply, why in [
            (httpx.Response(400, json={"error": "invalid_grant"}), "HTTP 400: invalid_grant"),
            (httpx.Response(200, json={"access_token": "a"}), "no refresh token"),
            (
                httpx.Response(200, json={"refresh_token": "r", "scope": "openid"}),
                "mail access wasn't granted",
            ),
        ]:
            route.mock(return_value=reply)
            r = await c.post(f"/api/notify/channels/{cid}/sign-in", headers=h)
            state = parse_qs(urlsplit(r.json()["url"]).query)["state"][0]
            r = await c.post(
                "/api/notify/oauth/finish", json={"state": state, "code": "c"}, headers=h
            )
            assert r.status_code == 502 and why in r.text
        # only Google email channels sign in through the browser
        other = n.store.add(
            {"name": "m", "kind": "smtp", "project": None, "config": cfg(587), "secrets": {}},
            "admin",
        )
        r = await c.post(f"/api/notify/channels/{other}/sign-in", headers=h)
        assert r.status_code == 400


@respx.mock
async def test_project_sign_in(app, svc):  # noqa: F811
    respx.get(f"{EP1}/1.0/projects/web").mock(
        return_value=lxd_ok({"name": "web", "config": {"restricted": "true"}})
    )
    cid = google_channel(svc, project="web")
    n = svc.notifier
    # a project's sign-in reaches Google at the address just checked (pinned)
    pinned = respx.post("https://203.0.113.10/token").mock(
        return_value=httpx.Response(200, json={"refresh_token": "rt-p", "access_token": "a"})
    )
    async with client(app) as c:
        h = {CSRF_HEADER: await login(c, "pat", "patpw")}
        r = await c.post(f"/api/projects/web/notify/channels/{cid}/sign-in", headers=h)
        state = parse_qs(urlsplit(r.json()["url"]).query)["state"][0]
        r = await c.post("/api/notify/oauth/finish", json={"state": state, "code": "c"}, headers=h)
        assert r.json()["path"] == "/projects/web#notifications"
        assert pinned.calls.last.request.headers["host"] == "oauth2.googleapis.com"
        assert n.store.channel(cid)["secrets"]["refresh_token"] == "rt-p"  # noqa: S105
        # a right taken away meanwhile
        r = await c.post(f"/api/projects/web/notify/channels/{cid}/sign-in", headers=h)
        state = parse_qs(urlsplit(r.json()["url"]).query)["state"][0]
        svc.store.set_grant("pat", "web", "viewer")
        r = await c.post("/api/notify/oauth/finish", json={"state": state, "code": "c"}, headers=h)
        assert r.status_code == 403 and "needs the manage right" in r.text


def test_sign_ins_expire():
    s = oauth.SignIns()
    ch = {"id": 1, "config": {"client_id": "c"}, "secrets": {}}
    url = s.start(ch, "admin", "https://m/oauth/callback", 0)
    state = parse_qs(urlsplit(url).query)["state"][0]
    with pytest.raises(ValueError, match="expired"):
        s.take(state, "admin", oauth.SIGN_IN_SECONDS + 1)
    s.start(ch, "admin", "https://m/oauth/callback", 0)
    s.start(ch, "admin", "https://m/oauth/callback", oauth.SIGN_IN_SECONDS + 1)
    assert len(s.pending) == 1  # the old one dropped


def test_switching_to_oauth_drops_the_password():
    """Covered through the API in test_sign_in's PUT; here the form's hint data."""
    spec = kinds.KINDS["smtp"]
    assert spec.when["password"] == ("auth", ("password",))
    assert json.dumps(spec.when)  # sent as JSON


# --- review fixes (D96) -----------------------------------------------------------------


@respx.mock
async def test_no_unsigned_mail_when_xoauth2_isnt_taken(smtp_server):  # noqa: F811
    """smtplib takes a 503 to AUTH as success: a server that doesn't offer XOAUTH2 (or
    answers 503) must not get the message unsigned."""
    respx.post(MS).mock(return_value=token("good"))
    http = httpx.AsyncClient()
    pins = {"smtp.office365.com": "127.0.0.1"}
    for offered, why in [(None, "doesn't offer XOAUTH2"), ("PLAIN LOGIN", "doesn't offer")]:
        s, port = smtp_server(auth=offered)
        with pytest.raises(NotifyError, match=why):
            await kinds.send(http, "smtp", ms(port), SECRET, EV, None, pins=pins)
        assert s.mail == [] and s.auth == []
    await http.aclose()


async def test_xoauth2_503_after_offering(smtp_server, monkeypatch):  # noqa: F811
    s, port = smtp_server()
    import smtplib

    monkeypatch.setattr(smtplib.SMTP, "auth", lambda self, *a, **k: (503, b"already"))
    from mcui.notify import mail

    with pytest.raises(NotifyError, match="SMTP 503: the sign-in wasn't accepted"):
        await mail.send(ms(port, host="localhost"), SECRET, EV, None, token="good")  # noqa: S106
    assert s.mail == []


def test_expiry_bounded_and_cache_pruned():
    async def run():
        http = httpx.AsyncClient()
        with respx.mock:
            route = respx.post(MS)
            for odd in ("Infinity", 1e308, -5, "NaN"):
                oauth._tokens.clear()
                text = f'{{"access_token": "t", "expires_in": {odd}}}'
                route.mock(return_value=httpx.Response(200, text=text))
                await oauth.access_token(http, ms(), SECRET, 0)
                assert list(oauth._tokens.values())[0][1] == 3600.0
            # an expired entry goes when the next token is stored
            oauth._tokens.clear()
            route.mock(return_value=token("a", 60))
            await oauth.access_token(http, ms(), SECRET, 0)
            route.mock(return_value=token("b", 60))
            await oauth.access_token(http, ms(), {"client_secret": "other"}, 1000)
            assert len(oauth._tokens) == 1
        await http.aclose()

    import asyncio

    asyncio.run(run())


def test_pending_sign_ins_capped():
    s = oauth.SignIns()
    ch = {"id": 1, "config": {"client_id": "c"}, "secrets": {}}
    states = []
    for _ in range(20):
        url = s.start(ch, "pat", "https://m/oauth/callback", 0)
        states.append(parse_qs(urlsplit(url).query)["state"][0])
    s.start(ch, "admin", "https://m/oauth/callback", 0)
    assert sum(v.username == "pat" for v in s.pending.values()) == oauth.PENDING_PER_USER
    assert s.take(states[-1], "pat", 1).channel_id == 1  # the newest still works
    with pytest.raises(ValueError):
        s.take(states[0], "pat", 1)


@respx.mock
async def test_sign_in_details(app, svc):  # noqa: F811
    """PKCE checks out, expiry through the API, edits during the sign-in, a cluster route
    for a project's channel, settings that can't be typed, the server-change message."""
    import base64
    import hashlib

    n = svc.notifier
    cid = google_channel(svc)
    reply = httpx.Response(
        200, json={"refresh_token": "rt", "scope": "https://mail.google.com/", "access_token": "a"}
    )
    route = respx.post(GOOGLE).mock(return_value=reply)

    async def start(c, h, path=f"/api/notify/channels/{cid}/sign-in"):
        r = await c.post(path, headers=h)
        return {k: v[0] for k, v in parse_qs(urlsplit(r.json()["url"]).query).items()}

    async with client(app) as c:
        h = {CSRF_HEADER: await login(c, "admin", "adminpw")}
        finish = "/api/notify/oauth/finish"
        q = await start(c, h)
        await c.post(finish, json={"state": q["state"], "code": "c"}, headers=h)
        verifier = parse_qs(route.calls.last.request.content.decode())["code_verifier"][0]
        digest = hashlib.sha256(verifier.encode()).digest()
        assert base64.urlsafe_b64encode(digest).decode().rstrip("=") == q["code_challenge"]
        # expired, through the API
        q = await start(c, h)
        n.clock.t += oauth.SIGN_IN_SECONDS + 1
        r = await c.post(finish, json={"state": q["state"], "code": "c"}, headers=h)
        assert r.status_code == 400 and "expired" in r.text
        # the mailbox changed between start and finish: not stored
        q = await start(c, h)
        ch = n.store.channel(cid)
        n.store.update(cid, {**ch, "config": {**ch["config"], "username": "b@example.org"}})
        r = await c.post(finish, json={"state": q["state"], "code": "c"}, headers=h)
        assert r.status_code == 409
        # ...or during Google's answer: the edit survives, no token goes on
        q = await start(c, h)
        ch = n.store.channel(cid)

        def edit_meanwhile(request):
            now = n.store.channel(cid)
            n.store.update(cid, {**now, "secrets": {"client_secret": "rotated"}})
            return reply

        route.mock(side_effect=edit_meanwhile)
        r = await c.post(finish, json={"state": q["state"], "code": "c"}, headers=h)
        assert r.status_code == 409
        assert n.store.channel(cid)["secrets"] == {"client_secret": "rotated"}
        # deleted meanwhile
        q = await start(c, h)
        route.mock(side_effect=lambda request: (n.store.delete(cid), reply)[1])
        r = await c.post(finish, json={"state": q["state"], "code": "c"}, headers=h)
        assert r.status_code == 404
        # a project's channel isn't signed in to from Settings
        web = google_channel(svc, project="web")
        r = await c.post(f"/api/notify/channels/{web}/sign-in", headers=h)
        assert r.status_code == 400 and "project's page" in r.text
        # the refresh token can't be typed; a password goes on a switch to OAuth2
        pw = n.store.add(
            {
                "name": "pw",
                "kind": "smtp",
                "project": None,
                "config": cfg(587, username="u", auth="password"),
                "secrets": {"password": "p"},
            },
            "admin",
        )
        body = {
            "name": "pw",
            "kind": "smtp",
            "config": dict(ms()),
            "secrets": {"refresh_token": "x"},
        }
        r = await c.put(f"/api/notify/channels/{pw}", json=body, headers=h)
        assert r.status_code == 400 and "no setting refresh_token" in r.text
        body["secrets"] = {"client_secret": "cs"}
        r = await c.put(f"/api/notify/channels/{pw}", json=body, headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["secrets_set"] == ["client_secret"]
        # a signed-in channel moved to another server: asks only for what can be typed
        g = google_channel(svc)
        ch = n.store.channel(g)
        n.store.update(g, {**ch, "secrets": {**ch["secrets"], "refresh_token": "rt"}})
        body = {"name": "gmail", "kind": "smtp", "config": dict(google(port=465)), "secrets": {}}
        r = await c.put(f"/api/notify/channels/{g}", json=body, headers=h)
        assert r.status_code == 400
        assert r.json()["detail"] == "the server changed: enter client_secret again, or remove it"


@respx.mock
async def test_project_sign_in_needs_a_restricted_project(app, svc):  # noqa: F811
    project = respx.get(f"{EP1}/1.0/projects/web").mock(
        return_value=lxd_ok({"name": "web", "config": {"restricted": "true"}})
    )
    cid = google_channel(svc, project="web")
    async with client(app) as c:
        h = {CSRF_HEADER: await login(c, "pat", "patpw")}
        r = await c.post(f"/api/projects/web/notify/channels/{cid}/sign-in", headers=h)
        state = parse_qs(urlsplit(r.json()["url"]).query)["state"][0]
        project.mock(return_value=lxd_ok({"name": "web", "config": {"restricted": "false"}}))
        app.state.restricted_cache.clear()  # as after its 30 s
        r = await c.post("/api/notify/oauth/finish", json={"state": state, "code": "c"}, headers=h)
        assert r.status_code == 403 and "no longer restricted" in r.text


@respx.mock
async def test_project_channel_token_endpoint_is_pinned(svc, smtp_server, monkeypatch):  # noqa: F811
    """A project's Microsoft channel: the token request goes to the checked address."""
    import ipaddress

    from mcui.notify import net

    s, port = smtp_server()

    async def resolve(host, port_):
        return ["127.0.0.1"] if host == "smtp.office365.com" else ["203.0.113.20"]

    monkeypatch.setattr(net, "resolve", resolve)
    ok = {ipaddress.ip_address("127.0.0.1"), ipaddress.ip_address("203.0.113.20")}
    monkeypatch.setattr(net, "is_public", lambda a: a in ok)
    pinned = respx.post("https://203.0.113.20/contoso.example/oauth2/v2.0/token").mock(
        return_value=token("good")
    )
    n = svc.notifier
    ch = n.store.channel(
        n.store.add(
            {"name": "m", "kind": "smtp", "project": "web", "config": ms(port), "secrets": SECRET},
            "pat",
        )
    )
    await n.test(ch)
    assert pinned.calls.last.request.headers["host"] == "login.microsoftonline.com"
    assert len(s.mail) == 1


async def test_redirect_warning(app, svc):  # noqa: F811
    svc.settings.server.public_url = None
    async with client(app) as c:
        await login(c, "admin", "adminpw")
        info = (await c.get("/api/notify/oauth")).json()
    assert info["redirect_uri"].startswith("http://")
    assert "only https" in info["warning"]
