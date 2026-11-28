"""Sending alerts out (D94): channel kinds, the notifier, its API."""

import asyncio
import hashlib
import hmac
import ipaddress
import json
import re

import httpx
import pytest
import respx

from mcui.alerts.engine import AlertEngine
from mcui.alerts.rules import Context, notify_failing
from mcui.alerts.store import Alert, AlertStore
from mcui.config import Settings
from mcui.main import create_app
from mcui.notify import kinds, net
from mcui.notify.kinds import NotifyError, check
from mcui.notify.net import NotPublic, check_public
from mcui.notify.store import BACKOFF
from mcui.services import build_services
from tests.conftest import EP1, fast_store, lxd_ok
from tests.test_api import CSRF_HEADER, client, login, lxd_pair

SLACK = "https://hooks.slack.com/services/T/B/x"
REAL_IS_PUBLIC = net.is_public
NTFY = "https://ntfy.example.org"


class Clock:
    t = 1_800_000_000.0

    def __call__(self) -> float:
        return self.t


def alert(id_=1, severity="warning", project=None, rule="r", title="t", **kw):
    return {
        "id": id_,
        "key": f"{rule}:{id_}",
        "rule": rule,
        "subject": str(id_),
        "severity": severity,
        "title": title,
        "detail": "d",
        "link": "/x",
        "project": project,
        "fired_at": Clock.t - 120,
        "resolved_at": None,
        "ack_by": None,
        **kw,
    }


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    """Names resolve to a public address unless a test says otherwise."""
    table = {"internal.example.org": ["10.0.0.5"], "v6.example.org": ["::ffff:127.0.0.1"]}

    async def resolve(host, port):
        try:
            return [str(ipaddress.ip_address(host))]  # an address, not a name
        except ValueError:
            pass
        if host == "nowhere.invalid":
            raise OSError(-2, "Name or service not known")
        return table.get(host, ["203.0.113.10"])

    monkeypatch.setattr(net, "resolve", resolve)
    # documentation addresses stand in for public ones (no real addresses in tests)
    real = net.is_public
    doc = ipaddress.ip_network("203.0.113.0/24")
    monkeypatch.setattr(net, "is_public", lambda a: a in doc or real(a))


@pytest.fixture
def svc(tmp_path):
    store = fast_store()
    store.add_user("admin", "adminpw", "admin")
    store.add_user("viewer", "viewerpw", "viewer")
    store.add_user("pat", "patpw", "project")
    store.set_grant("pat", "web", "admin")
    store.set_grant("pat", "lab", "operator")
    settings = Settings(
        server={
            "data_dir": str(tmp_path),
            "secure_cookies": False,
            "public_url": "https://mcui.example.org",
        }
    )
    s = build_services(settings, store=store, clients=lxd_pair())
    s.notifier.clock = s.notifier.store.clock = Clock()
    return s


def channel(n, kind="slack", project=None, **over):
    ch = {
        "name": over.pop("name", f"{kind}-{project or 'cluster'}"),
        "kind": kind,
        "project": project,
        "config": over.pop("config", {"reminder_hours": 4}),
        "secrets": over.pop("secrets", {"url": SLACK}),
        **over,
    }
    return n.store.add(ch, "admin")


# --- kinds ----------------------------------------------------------------------------


def test_check_settings():
    check("slack", {}, {"url": SLACK}, cluster=False)
    with pytest.raises(ValueError, match="url is required"):
        check("discord", {}, {}, cluster=True)
    with pytest.raises(ValueError, match="must use https"):
        check("slack", {}, {"url": "http://hooks.example.org/x"}, cluster=True)
    # plain http only for a cluster admin's own webhook or ntfy server
    check("webhook", {}, {"url": "http://10.0.0.5/hook"}, cluster=True)
    with pytest.raises(ValueError, match="must use https"):
        check("webhook", {}, {"url": "http://10.0.0.5/hook"}, cluster=False)
    with pytest.raises(ValueError, match="https:// URL"):
        check("teams", {}, {"url": "ftp://x"}, cluster=True)
    with pytest.raises(ValueError, match="own fields"):
        check("webhook", {}, {"url": "https://u:p@x.example.org/"}, cluster=True)
    ntfy = {"server": NTFY, "topic": "mcui-a1b2"}
    check("ntfy", ntfy, {}, cluster=False)
    with pytest.raises(ValueError, match="topic"):
        check("ntfy", {**ntfy, "topic": "a/b"}, {}, cluster=True)
    with pytest.raises(ValueError, match="not both"):
        check("ntfy", {**ntfy, "username": "u"}, {"token": "t", "password": "p"}, cluster=True)
    with pytest.raises(ValueError, match="needs a password"):
        check("ntfy", {**ntfy, "username": "u"}, {}, cluster=True)
    check("telegram", {"chat_id": "-1001234567890"}, {"token": "1:x"}, cluster=True)
    check("telegram", {"chat_id": "@my_alerts"}, {"token": "1:x"}, cluster=True)
    with pytest.raises(ValueError, match="chat_id"):
        check("telegram", {"chat_id": "me"}, {"token": "1:x"}, cluster=True)
    assert kinds.targets("telegram", {}, {"token": "1:x"}) == ["https://api.telegram.org"]
    assert kinds.targets("ntfy", ntfy, {"token": "t"}) == [NTFY]


def test_what_counts_as_public():
    ip = ipaddress.ip_address
    for a in ("10.0.0.5", "192.168.1.1", "127.0.0.1", "169.254.169.254", "::1", "fd00::1"):
        assert not REAL_IS_PUBLIC(ip(a)), a
    assert not REAL_IS_PUBLIC(ip("::ffff:10.0.0.5"))  # IPv4 inside IPv6
    assert not REAL_IS_PUBLIC(ip("203.0.113.10"))  # documentation: not routed
    # IPv4 carried in other IPv6 forms, multicast and old ranges (D94 review)
    for a in (
        "64:ff9b::a00:5",
        "64:ff9b::a9fe:a9fe",
        "::a00:5",
        "::127.0.0.1",
        "::ffff:0:a00:5",
        "2002:a00:5::",
        "192.88.99.1",
        "224.0.0.1",
        "ff02::1",
        "fec0::1",
    ):
        assert not REAL_IS_PUBLIC(ip(a)), a
    assert REAL_IS_PUBLIC(ip("2606:4700::1111"))


async def test_public_addresses_only():
    assert await check_public(["https://hooks.slack.com/x"]) == {"hooks.slack.com": "203.0.113.10"}
    for url in (
        "https://internal.example.org/x",
        "https://v6.example.org/",
        "https://127.0.0.1/",
        "https://nowhere.invalid/",
    ):
        # the same answer whatever the reason, and without the address (review)
        with pytest.raises(NotPublic, match="isn't a public address") as e:
            await check_public([url])
        assert "10.0.0.5" not in str(e.value) and "127.0.0.1/" not in str(e.value)


@respx.mock
async def test_pinned_transport():
    """A project channel connects to the address it checked, keeping the name for Host and
    TLS: a name that answers differently the second time can't lead inside (review)."""
    route = respx.post("https://203.0.113.10/hook").mock(return_value=httpx.Response(200))
    t = net.PinnedTransport({"hooks.example.org": "203.0.113.10"}, httpx.AsyncHTTPTransport())
    async with httpx.AsyncClient(transport=t) as http:
        await http.post("https://hooks.example.org/hook", json={})
        req = route.calls.last.request
        assert req.headers["host"] == "hooks.example.org"
        assert req.extensions["sni_hostname"] == "hooks.example.org"
        with pytest.raises(httpx.ConnectError, match="wasn't checked"):
            await http.post("https://elsewhere.example.org/")


def test_alert_text_cant_mention_or_link():
    """A channel name or a server's reply reaches other channels through the failing-
    channel alert; LXD names are in titles: none may ping everyone or add a link (review)."""
    evil = "<!channel> @here [sign in](https://evil.example) <https://evil.example|x> *b*"
    ev = [{"event": "fired", "alert": alert(title=evil, detail=evil)}]
    sl = json.dumps(kinds.slack(ev, None))
    assert "<!channel>" not in sl and "<https://evil" not in sl and "@here" not in sl
    assert "&lt;!channel&gt;" in sl
    di = kinds.discord(ev, None)
    assert "\\[sign in\\]\\(https" in di["embeds"][0]["description"]
    assert "\\*b\\*" in di["embeds"][0]["title"]
    card = kinds.teams(ev, None)["attachments"][0]["content"]
    texts = [i for i in card["body"] if i["type"] == "TextBlock"]
    assert texts == []  # all alert text in TextRuns, which Teams doesn't read as markdown
    assert "&lt;!channel&gt;" in kinds.telegram_text(ev, None)


def test_message_sizes():
    """Each service's limits, cut without breaking markup (review)."""
    many = [
        {"event": "fired", "alert": alert(i, detail="a&b<c> " * 300, title=f"t{i}")}
        for i in range(12)
    ]
    t = kinds.telegram_text(many, "https://m.example.org")
    assert len(t) <= kinds.TELEGRAM_LIMIT
    assert t.count("<b>") == t.count("</b>") and t.count("<a ") == t.count("</a>")
    assert not re.search(r"&[a-z]*…", t)  # no entity cut in half
    d = kinds.discord(many, None)
    total = sum(len(e["title"]) + len(e["description"]) for e in d["embeds"])
    assert total <= 6000 and len(d["embeds"]) == kinds.MAX_EVENTS
    assert len(kinds.plain(many)) <= 3800


def test_messages():
    fired = {"event": "fired", "alert": alert(title="osd.2 <b>full</b>", severity="critical")}
    gone = {
        "event": "resolved",
        "alert": alert(2, project="web", resolved_at=Clock.t, fired_at=Clock.t - 7500),
    }
    url = "https://mcui.example.org"
    assert kinds.summary([fired]) == "🔴 CRITICAL: osd.2 <b>full</b>"
    assert kinds.summary([fired, gone]) == "🔴 2 alert changes, 1 critical"
    assert kinds.body(gone) == "d (lasted 2 h 5 min) [project web]"
    s = kinds.slack([fired, gone], url)
    assert s["attachments"][0]["title_link"] == "https://mcui.example.org/x"
    assert s["attachments"][1]["title"] == "Resolved: t"
    d = kinds.discord([fired], None)
    assert d["allowed_mentions"] == {"parse": []} and "url" not in d["embeds"][0]
    card = kinds.teams([fired], url)["attachments"][0]["content"]
    assert card["type"] == "AdaptiveCard" and card["actions"][0]["url"] == f"{url}/alerts"
    assert card["body"][1]["inlines"][0]["color"] == "Attention"
    # Telegram's HTML: an alert's text can't add markup
    t = kinds.telegram_text([fired], url)
    assert "osd.2 &lt;b&gt;full&lt;/b&gt;" in t and '<a href="https://mcui.example.org/x">' in t
    many = [{"event": "reminder", "alert": alert(i, "critical")} for i in range(12)]
    assert kinds.label(many[0]) == "Still CRITICAL"
    assert "…and 2 more" in kinds.plain(many)
    assert len(kinds.telegram_text([{**fired, "alert": alert(detail="x" * 5000)}], None)) <= 4000
    assert kinds._duration(30) == "1 min" and kinds._duration(3 * 86400) == "3 days"


@respx.mock
async def test_send_each_kind():
    http = httpx.AsyncClient()
    ev = [{"event": "fired", "alert": alert()}]
    slack = respx.post(SLACK).mock(return_value=httpx.Response(200, text="ok"))
    await kinds.send(http, "slack", {}, {"url": SLACK}, ev, None)
    assert json.loads(slack.calls.last.request.content)["text"] == "⚠️ WARNING: t"
    hook = respx.post("https://hook.example.org/a").mock(return_value=httpx.Response(204))
    await kinds.send(
        http, "webhook", {}, {"url": "https://hook.example.org/a", "signing_secret": "k"}, ev, None
    )
    req = hook.calls.last.request
    sig = hmac.new(b"k", req.content, hashlib.sha256).hexdigest()
    assert req.headers["x-mcui-signature"] == f"sha256={sig}"
    assert json.loads(req.content)["events"][0]["alert"]["title"] == "t"
    for kind in ("discord", "teams"):
        r = respx.post(f"https://{kind}.example.org/").mock(return_value=httpx.Response(202))
        await kinds.send(http, kind, {}, {"url": f"https://{kind}.example.org/"}, ev, None)
        assert r.called
    tg = respx.post("https://api.telegram.org/bot1:abc/sendMessage").mock(
        return_value=httpx.Response(400, json={"ok": False, "description": "chat not found"})
    )
    with pytest.raises(NotifyError, match="HTTP 400: .*chat not found"):
        await kinds.send(http, "telegram", {"chat_id": "5"}, {"token": "1:abc"}, ev, None)
    assert json.loads(tg.calls.last.request.content)["parse_mode"] == "HTML"
    nt = respx.post(NTFY).mock(return_value=httpx.Response(200))
    cfg = {"server": NTFY + "/", "topic": "alerts"}
    await kinds.send(http, "ntfy", cfg, {"token": "tk"}, ev, "https://m.example.org")
    req = nt.calls.last.request
    msg = json.loads(req.content)
    assert req.headers["authorization"] == "Bearer tk"
    assert msg["topic"] == "alerts" and msg["priority"] == 3 and msg["title"] == "WARNING: t"
    assert msg["click"] == "https://m.example.org/x" and msg["tags"] == ["warning"]
    assert msg["message"] == "d"  # the title isn't repeated (seen live on ntfy.sh)
    two = [*ev, {"event": "escalated", "alert": alert(2, "critical")}]
    await kinds.send(http, "ntfy", {**cfg, "username": "u"}, {"password": "p"}, two, "https://m")
    req = nt.calls.last.request
    msg = json.loads(req.content)
    assert req.headers["authorization"].startswith("Basic ")
    assert msg["priority"] == 5 and msg["title"] == "2 alert changes, 1 critical"
    assert msg["click"] == "https://m/alerts"
    await kinds.send(http, "ntfy", cfg, {}, two, None)
    assert "click" not in json.loads(nt.calls.last.request.content)
    updates = {
        "result": [
            {"message": {"chat": {"id": 42, "type": "private", "first_name": "Ann"}}},
            {"my_chat_member": {"chat": {"id": -100, "type": "group", "title": "Ops"}}},
            {"message": {"chat": {"id": 42, "type": "private", "first_name": "Ann"}}},
            {"poll": {}},
        ]
    }
    respx.get("https://api.telegram.org/bot1:abc/getUpdates").mock(
        return_value=httpx.Response(200, json=updates)
    )
    assert await kinds.telegram_chats(http, "1:abc") == [
        {"id": "42", "type": "private", "name": "Ann"},
        {"id": "-100", "type": "group", "name": "Ops"},
    ]
    await http.aclose()


# --- the notifier ---------------------------------------------------------------------


@respx.mock
async def test_who_gets_what(svc):
    n = svc.notifier
    everyone = channel(n, name="all")
    crit = channel(n, name="crit", min_severity="critical")
    only_lab = channel(n, name="lab-only", config={"projects": ["lab"], "exclude_rules": ["noisy"]})
    web = channel(n, name="web", project="web", secrets={"url": "https://web.example.org/h"})
    channel(n, name="off", enabled=False)
    cluster_warn = alert(1)
    web_crit = alert(2, "critical", project="web")
    lab_noisy = alert(3, project="lab", rule="noisy")
    lab_warn = alert(4, project="lab")
    n.handle([("fired", a) for a in (cluster_warn, web_crit, lab_noisy, lab_warn)])
    got = {d["channel_id"]: [e["alert"]["id"] for e in d["events"]] for d in n.store.due()}
    assert got == {everyone: [1, 2, 3, 4], crit: [2], only_lab: [4], web: [2]}
    for d in n.store.due():
        n.store.sent(d["id"])
    # clearing: only to channels that were told it fired
    n.handle([("resolved", {**cluster_warn, "resolved_at": Clock.t})])
    assert {d["channel_id"] for d in n.store.due()} == {everyone}
    assert n.store.last_sent(everyone, 1) is None  # forgotten once resolved
    assert n.handle([]) == 0


@respx.mock
async def test_reminders(svc):
    n = svc.notifier
    ch = channel(n)
    quiet = channel(n, name="no reminders", config={"reminder_hours": 0})
    crit = alert(1, "critical")
    n.handle([("fired", crit), ("fired", alert(2))])
    for d in n.store.due():
        n.store.sent(d["id"])
    assert n.reminders([crit, alert(2)]) == 0  # too soon
    n.clock.t += 4 * 3600
    assert n.reminders([crit, alert(2), {**alert(3, "critical"), "ack_by": "admin"}]) == 1
    [d] = n.store.due()
    assert d["channel_id"] == ch and d["events"] == [{"event": "reminder", "alert": crit}]
    assert n.reminders([crit]) == 0  # sent again just now
    assert n.reminders([]) == 0
    assert n.store.last_sent(quiet, 1) is not None


@respx.mock
async def test_flush_retries_and_gives_up(svc):
    n = svc.notifier
    ok = channel(n, name="ok")
    bad = channel(n, name="bad", secrets={"url": "https://down.example.org/x"})
    web = channel(n, name="web", project="web", secrets={"url": "https://internal.example.org/x"})
    respx.post(SLACK).mock(return_value=httpx.Response(200))
    down = respx.post("https://down.example.org/x").mock(side_effect=httpx.ConnectError("refused"))
    inside = respx.post("https://internal.example.org/x").mock(return_value=httpx.Response(200))
    n.handle([("fired", alert(1, project="web"))])
    assert await n.flush() == 1
    assert not inside.called  # its name now leads inside: refused before sending
    assert "isn't a public address" in n.store.deliveries(web)[0]["error"]
    assert n.store.deliveries(ok)[0]["status"] == "sent"
    d = n.store.deliveries(bad)[0]
    assert d["status"] == "pending" and d["error"].startswith("can't connect")
    assert await n.flush() == 0  # not due yet
    for wait in BACKOFF:
        n.clock.t += wait
        await n.flush()
    assert down.call_count == len(BACKOFF) + 1
    assert n.store.deliveries(bad)[0]["status"] == "failed"
    assert {f["channel_id"] for f in n.store.failing()} == {bad, web}
    # the failing-channel alert, linked to where it's fixed
    ctx = Context(svc, {}, {}, n.clock())
    out = {a.subject: a for a in await notify_failing(ctx)}
    assert out[str(bad)].title == "Notifications to bad are failing"
    assert out[str(bad)].detail.startswith("Messages fail (can't connect; its Log has more)")
    assert out[str(web)].detail.startswith("Messages fail (not a public address;")
    assert out[str(bad)].link == "/settings#notifications"
    assert out[str(web)].project == "web" and out[str(web)].link == "/projects/web#notifications"
    # switched off: its queue is dropped, and it isn't "failing"
    n.handle([("fired", alert(5))])
    n.store.update(bad, {**n.store.channel(bad), "enabled": False})
    await n.flush()
    assert n.store.deliveries(bad)[0]["error"] == "the channel was switched off"
    assert n.store.deliveries(bad)[0]["status"] == "dropped"
    assert str(bad) not in {a.subject for a in await notify_failing(ctx)}
    # a channel deleted with deliveries queued
    n.handle([("fired", alert(6))])
    n.store.delete(ok)
    await n.flush()
    assert n.store.channel(ok) is None and n.store.deliveries(ok) == []
    n.clock.t += 200 * 86400
    assert n.store.prune(90) > 0


@respx.mock
async def test_test_message_clears_failing(svc):
    n = svc.notifier
    ch_id = channel(n)
    route = respx.post(SLACK).mock(return_value=httpx.Response(500, text="no"))
    with pytest.raises(NotifyError, match="HTTP 500: no"):
        await n.test(n.store.channel(ch_id))
    assert [f["channel_id"] for f in n.store.failing()] == [ch_id]
    route.mock(return_value=httpx.Response(200))
    await n.test(n.store.channel(ch_id))
    assert n.store.failing() == []
    assert "Channel slack-cluster works." in route.calls.last.request.content.decode()
    assert await n.flush() == 0  # a test isn't queued


def test_engine_reports_changes():
    clock = Clock()
    store = AlertStore(fast_store().db, clock)
    eng = AlertEngine(store, clock)
    a = Alert("r", "s", "warning", "t", "d")
    eng.apply({"r": [a]}, {})
    assert [e for e, _ in eng.changes] == ["fired"]
    eng.apply({"r": [a]}, {})
    assert eng.changes == []
    eng.apply({"r": [Alert("r", "s", "critical", "t", "d")]}, {})
    assert [(e, r["severity"]) for e, r in eng.changes] == [("escalated", "critical")]
    eng.apply({"r": []}, {})
    assert [(e, r["resolved_at"] is not None) for e, r in eng.changes] == [("resolved", True)]


@respx.mock
async def test_alert_round_sends(svc, monkeypatch):
    """The alert loop hands its changes to the notifier, and a broken notifier doesn't
    stop alerting."""
    n = svc.notifier
    channel(n)
    route = respx.post(SLACK).mock(return_value=httpx.Response(200))
    svc.alerts.engine.changes = [("fired", alert())]
    await svc.alerts.notify()
    await n.task  # sent in the background, not in the round
    assert route.called
    await n.aclose()

    def boom(changes):
        raise RuntimeError("x")

    monkeypatch.setattr(n, "handle", boom)
    await svc.alerts.notify()  # logged, not raised
    svc.notifier = None
    await svc.alerts.notify()


# --- the API --------------------------------------------------------------------------


@pytest.fixture
def app(svc):
    return create_app(services=svc, static_dir=None)


SLACK_IN = {"name": "ops", "kind": "slack", "secrets": {"url": SLACK}}


@respx.mock
async def test_cluster_channels_api(app, svc):
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        assert (await c.get("/api/notify/channels")).status_code == 403
        assert (await c.get("/api/notify/kinds")).json()["ntfy"]["defaults"] == {
            "server": "https://ntfy.sh"
        }
        h = {CSRF_HEADER: await login(c, "admin", "adminpw")}
        r = await c.post("/api/notify/channels", json=SLACK_IN, headers=h)
        assert r.status_code == 201, r.text
        ch = r.json()
        assert ch["secrets_set"] == ["url"] and SLACK not in r.text  # write-only
        assert ch["reminder_hours"] == 4 and ch["last"] is None
        cid = ch["id"]
        # a change without the secret keeps it; "" removes an optional one
        r = await c.put(
            f"/api/notify/channels/{cid}",
            json={
                **SLACK_IN,
                "secrets": {},
                "min_severity": "critical",
                "exclude_rules": ["ceph_raw"],
            },
            headers=h,
        )
        assert r.json()["min_severity"] == "critical" and r.json()["secrets_set"] == ["url"]
        assert svc.notifier.store.channel(cid)["secrets"]["url"] == SLACK
        bad = [
            ({**SLACK_IN, "kind": "discord"}, "kind can't change"),
            ({**SLACK_IN, "secrets": {"token": "x"}}, "no setting token"),
            ({**SLACK_IN, "exclude_rules": ["nope"]}, "no such rule"),
            ({**SLACK_IN, "secrets": {"url": ""}}, "url is required"),
        ]
        for body, why in bad:
            r = await c.put(f"/api/notify/channels/{cid}", json=body, headers=h)
            assert r.status_code == 400 and why in r.text, r.text
        # a cluster admin may use a LAN server, and limit a channel to projects
        lan = {
            "name": "lan",
            "kind": "ntfy",
            "config": {"server": "http://10.0.0.5:8080", "topic": "ops"},
            "projects": ["web"],
        }
        r = await c.post("/api/notify/channels", json=lan, headers=h)
        assert r.status_code == 201 and r.json()["config"]["server"] == "http://10.0.0.5:8080"
        route = respx.post(SLACK).mock(return_value=httpx.Response(200))
        assert (await c.post(f"/api/notify/channels/{cid}/test", headers=h)).json() == {"ok": True}
        route.mock(return_value=httpx.Response(404, text="no_service"))
        r = await c.post(f"/api/notify/channels/{cid}/test", headers=h)
        assert r.json() == {"ok": False, "error": "HTTP 404: no_service"}
        log = (await c.get(f"/api/notify/channels/{cid}/deliveries")).json()
        assert [d["status"] for d in log] == ["failed", "sent"]
        assert log[0]["events"][0]["title"] == "Test notification from microcloud-ui"
        listed = (await c.get("/api/notify/channels")).json()
        assert [x["name"] for x in listed] == ["ops", "lan"]
        assert listed[0]["last"]["status"] == "failed"
        assert (await c.delete(f"/api/notify/channels/{cid}", headers=h)).status_code == 204
        assert (await c.delete(f"/api/notify/channels/{cid}", headers=h)).status_code == 404
        actions = [e["action"] for e in svc.store.audit_entries(limit=10)]
        assert actions[:6] == [
            "notify.delete",
            "notify.test",
            "notify.test",
            "notify.create",
            "notify.update",
            "notify.create",
        ]
        assert svc.store.audit_entries(limit=10)[4]["detail"] is None  # no secret changed


@respx.mock
async def test_project_channels_api(app, svc):
    respx.get(f"{EP1}/1.0/projects/web").mock(
        return_value=lxd_ok({"name": "web", "config": {"restricted": "true"}})
    )
    cluster_ch = channel(svc.notifier, name="cluster")
    async with client(app) as c:
        h = {CSRF_HEADER: await login(c, "pat", "patpw")}
        base = "/api/projects/web/notify/channels"
        r = await c.post(base, json=SLACK_IN, headers=h)
        assert r.status_code == 201, r.text
        cid = r.json()["id"]
        assert r.json()["project"] == "web"
        # only public servers, https, and no other projects' alerts
        for body, why in [
            (
                {**SLACK_IN, "secrets": {"url": "https://internal.example.org/x"}},
                "isn't a public address",
            ),
            ({**SLACK_IN, "kind": "webhook", "secrets": {"url": "http://h.example.org/"}}, "https"),
            ({**SLACK_IN, "projects": ["lab"]}, "only its project's alerts"),
        ]:
            r = await c.post(base, json=body, headers=h)
            assert r.status_code == 400 and why in r.text, r.text
        assert [x["id"] for x in (await c.get(base)).json()] == [cid]
        # not another project's, nor the cluster's
        for url in (f"{base}/{cluster_ch}", "/api/projects/lab/notify/channels"):
            r = await c.put(url, json=SLACK_IN, headers=h)
            assert r.status_code in (403, 404, 405)
        assert (await c.get("/api/projects/lab/notify/channels")).status_code == 403
        assert (await c.get("/api/projects/other/notify/channels")).status_code == 404
        assert (await c.get("/api/notify/channels")).status_code == 403
        r = await c.delete(f"{base}/{cluster_ch}", headers=h)
        assert r.status_code == 404
        # sent to the address checked, by name for TLS and Host (pinned, review)
        route = respx.post("https://203.0.113.10/services/T/B/x").mock(
            return_value=httpx.Response(200)
        )
        r = await c.post(f"{base}/{cid}/test", headers=h)
        assert r.json() == {"ok": True} and route.called
        assert route.calls.last.request.headers["host"] == "hooks.slack.com"
        assert len((await c.get(f"{base}/{cid}/deliveries")).json()) == 1
        r = await c.put(f"{base}/{cid}", json={**SLACK_IN, "name": "ops2"}, headers=h)
        assert r.json()["name"] == "ops2"
        # Telegram: chats seen by a new bot's token, or a saved channel's
        respx.get("https://api.telegram.org/bot9:t/getUpdates").mock(
            return_value=httpx.Response(200, json={"result": []})
        )
        p = "/api/projects/web/notify/telegram-chats"
        assert (await c.post(p, json={"token": "9:t"}, headers=h)).json() == []
        r = await c.post(p, json={"channel_id": cid}, headers=h)  # a Slack channel: no token
        assert r.status_code == 400
        r = await c.post(p, json={"channel_id": cluster_ch}, headers=h)
        assert r.status_code == 404
        assert (await c.delete(f"{base}/{cid}", headers=h)).status_code == 204


@respx.mock
async def test_telegram_chats_api(app, svc):
    tg = channel(svc.notifier, "telegram", config={"chat_id": "1"}, secrets={"token": "7:k"})
    respx.get("https://api.telegram.org/bot7:k/getUpdates").mock(
        return_value=httpx.Response(401, json={"description": "Unauthorized"})
    )
    async with client(app) as c:
        h = {CSRF_HEADER: await login(c, "admin", "adminpw")}
        r = await c.post("/api/notify/telegram-chats", json={"channel_id": tg}, headers=h)
        assert r.status_code == 502 and "Unauthorized" in r.text
        r = await c.post("/api/notify/telegram-chats", json={}, headers=h)
        assert r.status_code == 400


@respx.mock
async def test_project_rename_and_delete_take_channels(app, svc):
    n = svc.notifier
    web = channel(n, project="web")
    respx.post(f"{EP1}/1.0/projects/web").mock(
        return_value=httpx.Response(
            202,
            json={"type": "async", "operation": "/1.0/operations/op1", "metadata": {"id": "op1"}},
        )
    )
    respx.get(f"{EP1}/1.0/operations/op1/wait").mock(
        return_value=lxd_ok({"id": "op1", "status": "Success", "status_code": 200})
    )
    respx.delete(f"{EP1}/1.0/projects/www").mock(
        return_value=httpx.Response(
            202,
            json={"type": "async", "operation": "/1.0/operations/op2", "metadata": {"id": "op2"}},
        )
    )
    respx.get(f"{EP1}/1.0/operations/op2/wait").mock(
        return_value=lxd_ok({"id": "op2", "status": "Success", "status_code": 200})
    )
    async with client(app) as c:
        h = {CSRF_HEADER: await login(c, "admin", "adminpw")}
        r = await c.post("/api/projects/web/rename", json={"name": "www"}, headers=h)
        assert r.status_code == 200, r.text
        assert n.store.channel(web)["project"] == "www"
        assert (await c.delete("/api/projects/www", headers=h)).status_code == 200
        assert n.store.channel(web) is None


# --- review fixes (D94) -----------------------------------------------------------------


def test_project_channel_leaves_out_rules(svc):
    n = svc.notifier
    web = channel(n, project="web", config={"exclude_rules": ["instance_oom"]})
    n.handle([("fired", alert(1, project="web", rule="instance_oom"))])
    n.handle([("fired", alert(2, project="web", rule="instance_rootfs"))])
    assert [[e["alert"]["id"] for e in d["events"]] for d in n.store.deliveries(web)] == [[2]]


@respx.mock
async def test_slow_or_huge_replies(svc, monkeypatch):
    """A server that dribbles its reply can't hold the notifier up past SEND_SECONDS, and
    a huge reply isn't kept; redirects fail rather than count as sent."""
    from mcui.notify import service

    monkeypatch.setattr(service, "SEND_SECONDS", 0.2)
    n = svc.notifier
    ch = n.store.channel(channel(n))

    async def slow(request):
        await asyncio.sleep(5)
        return httpx.Response(200)

    route = respx.post(SLACK).mock(side_effect=slow)
    with pytest.raises(NotifyError, match="timed out"):
        await n.test(ch)
    route.mock(return_value=httpx.Response(500, content=b"x" * 10**6))
    with pytest.raises(NotifyError) as e:
        await n.test(ch)
    assert len(str(e.value)) < 300
    route.mock(return_value=httpx.Response(302, headers={"location": "https://elsewhere/"}))
    with pytest.raises(NotifyError, match="redirected"):
        await n.test(ch)


@respx.mock
async def test_errors_never_carry_secrets(svc):
    n = svc.notifier
    ch = n.store.channel(
        channel(n, "ntfy", config={"server": NTFY, "topic": "t"}, secrets={"token": "tok-123"})
    )
    respx.post(NTFY).mock(side_effect=httpx.ConnectError("bad header Bearer tok-123"))
    with pytest.raises(NotifyError) as e:
        await n.test(ch)
    assert "tok-123" not in str(e.value) and "***" in str(e.value)
    assert "tok-123" not in n.store.deliveries(ch["id"])[0]["error"]


async def test_background_sending(svc):
    n = svc.notifier
    gate = asyncio.Event()

    async def flush():
        await gate.wait()
        return 0

    n.flush = flush
    n.kick()
    first = n.task
    n.kick()  # still running: no second task
    assert n.task is first
    gate.set()
    await first
    await n.aclose()


def test_project_filters_follow_renames(svc):
    n = svc.notifier
    two = channel(n, name="two", config={"projects": ["web", "lab"]})
    one = channel(n, name="one", config={"projects": ["web"]})
    n.store.rename_project("web", "www")
    assert n.store.channel(two)["config"]["projects"] == ["www", "lab"]
    n.store.forget_project("www")
    assert n.store.channel(two)["config"]["projects"] == ["lab"]
    # left with none, it would get every alert: switched off instead
    assert n.store.channel(one)["config"]["projects"] == []
    assert not n.store.channel(one)["enabled"]
    assert n.store.channel(two)["enabled"]


def test_prune_forgets_cleared_alerts(svc):
    n = svc.notifier
    store = svc.alerts.store
    open_id = store.fire(Alert("r", "a", "critical", "t", "d"))
    gone_id = store.fire(Alert("r", "b", "critical", "t", "d"))
    store.resolve_all()  # outside a round: no "resolved" change
    ch = channel(n)
    n.store.mark_sent(ch, gone_id)
    store.db.execute("UPDATE alerts SET resolved_at = NULL WHERE id = ?", (open_id,))
    n.store.mark_sent(ch, open_id)
    n.store.prune(90)
    assert n.store.last_sent(ch, gone_id) is None and n.store.last_sent(ch, open_id) is not None


async def test_saving_checks(app, svc):
    n = svc.notifier
    ntfy = {
        "name": "phone",
        "kind": "ntfy",
        "config": {"server": NTFY, "topic": "t"},
        "secrets": {"token": "tk"},
    }
    async with client(app) as c:
        h = {CSRF_HEADER: await login(c, "admin", "adminpw")}
        cid = (await c.post("/api/notify/channels", json=ntfy, headers=h)).json()["id"]
        # pointing it elsewhere needs the token again: it mustn't follow to a new server
        moved = {**ntfy, "config": {"server": "https://other.example.org", "topic": "t"}}
        r = await c.put(f"/api/notify/channels/{cid}", json={**moved, "secrets": {}}, headers=h)
        assert r.status_code == 400 and "enter token again" in r.text
        r = await c.put(f"/api/notify/channels/{cid}", json=moved, headers=h)
        assert r.status_code == 200
        assert n.store.channel(cid)["config"]["server"] == "https://other.example.org"
        r = await c.put(
            f"/api/notify/channels/{cid}", json={**moved, "secrets": {"token": ""}}, headers=h
        )
        assert r.status_code == 200 and r.json()["secrets_set"] == []
        for body, why in [
            ({**ntfy, "secrets": {"token": "a\nb"}}, "printable ASCII"),
            ({**ntfy, "config": {"server": "https://h.example.org:99999", "topic": "t"}}, "port"),
            ({**ntfy, "name": "<!here>"}, "name"),
            ({**ntfy, "name": "   "}, "name"),
            ({**SLACK_IN, "projects": ["no/such"]}, "projects"),
        ]:
            r = await c.post("/api/notify/channels", json=body, headers=h)
            assert r.status_code in (400, 422) and why in r.text, r.text
