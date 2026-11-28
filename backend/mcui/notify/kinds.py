"""The kinds of notification channel (D94): what each needs, and how a batch of alert
events is written for it and sent.

An event is ``{"event": "fired" | "escalated" | "resolved" | "reminder", "alert": {...}}``,
the alert as the store keeps it. One round's events for a channel go out as one message.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

MAX_EVENTS = 10  # listed in one message; the rest are counted
TOPIC = re.compile(r"[A-Za-z0-9_-]{1,64}")
CHAT_ID = re.compile(r"-?\d{1,20}|@[A-Za-z0-9_]{5,32}")
TELEGRAM_API = "https://api.telegram.org"
PRINTABLE = re.compile(r"[\x20-\x7e]+")
REPLY_BYTES = 4096  # read of a reply, for its status and an error snippet


class NotifyError(Exception):
    """A send failed; the message says why, for the delivery log."""


@dataclass(frozen=True)
class Kind:
    label: str
    config: tuple[str, ...] = ()  # settings shown back to admins
    secrets: tuple[str, ...] = ()  # write-only
    required: tuple[str, ...] = ()
    defaults: dict[str, str] = field(default_factory=dict)


KINDS: dict[str, Kind] = {
    "slack": Kind("Slack (or Mattermost)", secrets=("url",), required=("url",)),
    "discord": Kind("Discord", secrets=("url",), required=("url",)),
    "teams": Kind("Microsoft Teams (Workflows)", secrets=("url",), required=("url",)),
    "webhook": Kind("Webhook (JSON)", secrets=("url", "signing_secret"), required=("url",)),
    "telegram": Kind(
        "Telegram", config=("chat_id",), secrets=("token",), required=("chat_id", "token")
    ),
    "ntfy": Kind(
        "ntfy",
        config=("server", "topic", "username"),
        secrets=("token", "password"),
        required=("server", "topic"),
        defaults={"server": "https://ntfy.sh"},
    ),
}
# their settings that are URLs the app will send to
URL_FIELDS = {"url", "server"}


def check(kind: str, config: dict[str, str], secrets: dict[str, str], *, cluster: bool) -> None:
    """Refuse settings that can't work, with a reason for the form. Project channels must
    use https (only cluster admins may point a channel at a plain-http server on their
    network); where it may go is checked separately (``net.check_public``)."""
    k = KINDS[kind]
    values = {**config, **secrets}
    for name in k.required:
        if not values.get(name):
            raise ValueError(f"{name} is required")
    for name in URL_FIELDS & values.keys():
        if not values[name]:
            continue
        u = urlsplit(values[name])
        plain_ok = cluster and kind in ("webhook", "ntfy")
        try:
            u.port  # noqa: B018 - raises for a port out of range
        except ValueError as e:
            raise ValueError(f"{name}: not a valid port") from e
        if u.scheme not in ("https", "http") or not u.hostname:
            raise ValueError(f"{name} must be an https:// URL")
        if u.scheme == "http" and not plain_ok:
            raise ValueError(f"{name} must use https")
        if u.username or u.password:
            raise ValueError(f"{name}: put credentials in their own fields, not the URL")
    for name, value in secrets.items():
        # sent in headers or URLs: printable ASCII only (a line break could also put the
        # value into an error message, D94 review)
        if value and not PRINTABLE.fullmatch(value):
            raise ValueError(f"{name}: printable ASCII characters only")
    if kind == "ntfy":
        if not TOPIC.fullmatch(config.get("topic", "")):
            raise ValueError("topic: letters, digits, - and _ only (up to 64)")
        if secrets.get("token") and (config.get("username") or secrets.get("password")):
            raise ValueError("use an access token or a username and password, not both")
        if bool(config.get("username")) != bool(secrets.get("password")):
            raise ValueError("a username needs a password, and the other way round")
    if kind == "telegram" and not CHAT_ID.fullmatch(config.get("chat_id", "")):
        raise ValueError("chat_id: a number such as -1001234567890, or @channelname")


def targets(kind: str, config: dict[str, str], secrets: dict[str, str]) -> list[str]:
    """The URLs a channel sends to, for the address check."""
    if kind == "telegram":
        return [TELEGRAM_API]
    return [v for n, v in {**config, **secrets}.items() if n in URL_FIELDS and v]


# --- writing ----------------------------------------------------------------------------

_LABEL = {
    "fired": "{SEV}",
    "escalated": "Now CRITICAL",
    "resolved": "Resolved",
    "reminder": "Still {SEV}",
}
_EMOJI = {"warning": "⚠️", "critical": "🔴", "resolved": "✅"}
_COLOUR = {"warning": "#e5a50a", "critical": "#c01c28", "resolved": "#2ec27e"}


def _state(e: dict[str, Any]) -> str:
    return "resolved" if e["event"] == "resolved" else str(e["alert"]["severity"])


def label(e: dict[str, Any]) -> str:
    return _LABEL[e["event"]].replace("{SEV}", str(e["alert"]["severity"]).upper())


def link(e: dict[str, Any], app_url: str | None) -> str | None:
    path = e["alert"].get("link") or "/alerts"
    return f"{app_url}{path}" if app_url else None


def _duration(seconds: float) -> str:
    m = int(seconds // 60)
    if m < 60:
        return f"{max(m, 1)} min"
    h, m = divmod(m, 60)
    return f"{h} h {m} min" if h < 48 else f"{h // 24} days"


def body(e: dict[str, Any]) -> str:
    a = e["alert"]
    text = str(a.get("detail") or "")
    if e["event"] == "resolved" and a.get("resolved_at"):
        text += f" (lasted {_duration(a['resolved_at'] - a['fired_at'])})"
    if a.get("project"):
        text += f" [project {a['project']}]"
    return text.strip()


def summary(events: list[dict[str, Any]]) -> str:
    if len(events) == 1:
        e = events[0]
        return f"{_EMOJI[_state(e)]} {label(e)}: {e['alert']['title']}"
    crit = sum(1 for e in events if _state(e) == "critical")
    return f"{_EMOJI['critical' if crit else 'warning']} {len(events)} alert changes" + (
        f", {crit} critical" if crit else ""
    )


def _more(events: list[dict[str, Any]]) -> str:
    extra = len(events) - MAX_EVENTS
    return f"…and {extra} more (see the Alerts page)" if extra > 0 else ""


def worst(events: list[dict[str, Any]]) -> str:
    states = {_state(e) for e in events}
    return "critical" if "critical" in states else "warning" if "warning" in states else "resolved"


# Alert text holds names from LXD, and a project admin's channel name and server reply
# reach the cluster's channels through the failing-channel alert, so it can't be allowed
# to mention everyone or add links of its own (D94 review).
_WORD_JOINER = "\u2060"
_MENTIONS = re.compile(r"@(all|channel|here|everyone)\b", re.IGNORECASE)


def _slack(text: str) -> str:
    """Slack's mrkdwn (and Mattermost's): no ``<!channel>``, ``<url|text>`` or ``@here``."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return _MENTIONS.sub(lambda m: f"@{_WORD_JOINER}{m.group(1)}", text)


_DISCORD_MD = re.compile(r"([\\*_~`|>\[\]()#-])")


def _discord(text: str) -> str:
    """Discord markdown: no masked links, formatting or headings from alert text."""
    return _DISCORD_MD.sub(r"\\\1", text)


def _cut(text: str, n: int) -> str:
    return text if len(text) <= n else text[: max(n - 1, 0)] + "…"


def slack(events: list[dict[str, Any]], app_url: str | None) -> dict[str, Any]:
    attachments = []
    for e in events[:MAX_EVENTS]:
        att = {
            "color": _COLOUR[_state(e)],
            "title": _slack(_cut(f"{label(e)}: {e['alert']['title']}", 300)),
            "text": _slack(_cut(body(e), 2000)),
        }
        if url := link(e, app_url):
            att["title_link"] = url
        attachments.append(att)
    out: dict[str, Any] = {"text": _slack(summary(events)), "attachments": attachments}
    if more := _more(events):
        out["attachments"].append({"text": more})
    return out


DISCORD_TOTAL = 5500  # Discord refuses more than 6000 characters across a message's embeds


def discord(events: list[dict[str, Any]], app_url: str | None) -> dict[str, Any]:
    shown = events[:MAX_EVENTS]
    content = _discord(summary(events)) + (f"\n{_more(events)}" if _more(events) else "")
    titles = [_discord(_cut(f"{label(e)}: {e['alert']['title']}", 200)) for e in shown]
    room = (DISCORD_TOTAL - sum(len(t) for t in titles)) // max(len(shown), 1)
    embeds = []
    for e, title in zip(shown, titles, strict=True):
        emb: dict[str, Any] = {
            "title": title[:256],
            "description": _discord(_cut(body(e), max(room // 2, 50)))[: max(room, 100)],
            "color": int(_COLOUR[_state(e)][1:], 16),
        }
        if url := link(e, app_url):
            emb["url"] = url
        embeds.append(emb)
    # no pinging @everyone through a crafted alert title
    return {"content": content[:2000], "embeds": embeds, "allowed_mentions": {"parse": []}}


def _run(text: str, **style: Any) -> dict[str, Any]:
    """Adaptive Card text that isn't read as markdown: a RichTextBlock's TextRun."""
    return {"type": "RichTextBlock", "inlines": [{"type": "TextRun", "text": text, **style}]}


def teams(events: list[dict[str, Any]], app_url: str | None) -> dict[str, Any]:
    """An Adaptive Card, as a Teams Workflows webhook takes it. Alert text goes in
    TextRuns, which Teams doesn't read as markdown (no links from alert text)."""
    items: list[dict[str, Any]] = [_run(summary(events), weight="Bolder")]
    colour = {"warning": "Warning", "critical": "Attention", "resolved": "Good"}
    for e in events[:MAX_EVENTS]:
        title = _cut(f"{label(e)}: {e['alert']['title']}", 300)
        items.append({**_run(title, color=colour[_state(e)], weight="Bolder"), "spacing": "Medium"})
        items.append({**_run(_cut(body(e), 2000)), "spacing": "None"})
    if more := _more(events):
        items.append(_run(more, isSubtle=True))
    card: dict[str, Any] = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": items,
    }
    if app_url:
        card["actions"] = [
            {"type": "Action.OpenUrl", "title": "Open alerts", "url": f"{app_url}/alerts"}
        ]
    return {
        "type": "message",
        "attachments": [
            {"contentType": "application/vnd.microsoft.card.adaptive", "content": card}
        ],
    }


def webhook(events: list[dict[str, Any]], app_url: str | None) -> dict[str, Any]:
    return {
        "source": "microcloud-ui",
        "sent_at": time.time(),
        "summary": summary(events),
        "events": [
            {"event": e["event"], "alert": e["alert"], "url": link(e, app_url)} for e in events
        ],
    }


TELEGRAM_LIMIT = 4000  # Telegram's is 4096, after entities are parsed


def telegram_text(events: list[dict[str, Any]], app_url: str | None) -> str:
    """Telegram HTML. Shortened by cutting each alert's text before it's escaped, so a cut
    never splits a tag or an entity (Telegram refuses the message then, D94 review)."""
    room = 3000
    while True:
        lines = [f"<b>{html.escape(summary(events))}</b>"] if len(events) > 1 else []
        shown = events[:MAX_EVENTS]
        each = room // max(len(shown), 1)
        for e in shown:
            title = html.escape(_cut(f"{label(e)}: {e['alert']['title']}", 200))
            if url := link(e, app_url):
                title = f'<a href="{html.escape(url, quote=True)}">{title}</a>'
            text = html.escape(_cut(body(e), each))
            lines.append(f"{_EMOJI[_state(e)]} <b>{title}</b>\n{text}")
        if more := _more(events):
            lines.append(html.escape(more))
        out = "\n\n".join(lines)
        if len(out) <= TELEGRAM_LIMIT or room < 100:
            return out
        room //= 2


def plain(events: list[dict[str, Any]], limit: int = 3800) -> str:
    """Plain text within ``limit`` characters (ntfy turns a longer message into an
    attachment)."""
    shown = events[:MAX_EVENTS]
    each = max((limit - 300) // max(len(shown), 1), 40)
    lines = [
        f"{_cut(label(e) + ': ' + str(e['alert']['title']), 200)}\n{_cut(body(e), each)}"
        for e in shown
    ]
    if more := _more(events):
        lines.append(more)
    return _cut("\n\n".join(lines), limit)


# --- sending ----------------------------------------------------------------------------


async def _post(http: httpx.AsyncClient, url: str, limit: int = REPLY_BYTES, **kw: Any) -> bytes:
    """POST and read at most ``limit`` bytes of the reply (a server can't make the app
    hold gigabytes); anything but 2xx fails, redirects too, as they aren't followed."""
    return await _request(http, "POST", url, limit, **kw)


async def _request(http: httpx.AsyncClient, method: str, url: str, limit: int, **kw: Any) -> bytes:
    async with http.stream(method, url, **kw) as r:
        data = b""
        async for chunk in r.aiter_bytes():
            data += chunk
            if len(data) >= limit:
                break
        if 300 <= r.status_code < 400:
            raise NotifyError(f"HTTP {r.status_code}: redirected (not followed)")
        if r.status_code >= 400:
            text = data[:200].decode("utf-8", "replace")
            snippet = re.sub(r"\s+", " ", text).strip()
            raise NotifyError(f"HTTP {r.status_code}" + (f": {snippet}" if snippet else ""))
        return data[:limit]


async def send(
    http: httpx.AsyncClient,
    kind: str,
    config: dict[str, str],
    secrets: dict[str, str],
    events: list[dict[str, Any]],
    app_url: str | None,
) -> None:
    """Send one message; raises ``NotifyError`` (or ``httpx.HTTPError``) on failure."""
    if kind == "slack":
        await _post(http, secrets["url"], json=slack(events, app_url))
    elif kind == "discord":
        await _post(http, secrets["url"], json=discord(events, app_url))
    elif kind == "teams":
        await _post(http, secrets["url"], json=teams(events, app_url))
    elif kind == "webhook":
        data = json.dumps(webhook(events, app_url)).encode()
        headers = {"content-type": "application/json"}
        if key := secrets.get("signing_secret"):
            sig = hmac.new(key.encode(), data, hashlib.sha256).hexdigest()
            headers["x-mcui-signature"] = f"sha256={sig}"
        await _post(http, secrets["url"], content=data, headers=headers)
    elif kind == "telegram":
        await _post(
            http,
            f"{TELEGRAM_API}/bot{quote(secrets['token'], safe=':')}/sendMessage",
            json={
                "chat_id": config["chat_id"],
                "text": telegram_text(events, app_url),
                "parse_mode": "HTML",
                "link_preview_options": {"is_disabled": True},
            },
        )
    elif kind == "ntfy":
        await _ntfy(http, config, secrets, events, app_url)
    else:  # pragma: no cover - kinds are checked when saved
        raise NotifyError(f"unknown kind {kind}")


_PRIORITY = {"critical": "5", "warning": "3", "resolved": "2"}
_TAGS = {"critical": "rotating_light", "warning": "warning", "resolved": "white_check_mark"}


async def _ntfy(
    http: httpx.AsyncClient,
    config: dict[str, str],
    secrets: dict[str, str],
    events: list[dict[str, Any]],
    app_url: str | None,
) -> None:
    sev = worst(events)
    one = events[0] if len(events) == 1 else None
    # headers are latin-1: the title goes in the JSON body, which ntfy reads as UTF-8
    msg: dict[str, Any] = {
        "topic": config["topic"],
        "title": _cut(f"{label(one)}: {one['alert']['title']}", 200)
        if one
        else summary(events)[2:].strip(),
        # one alert: its title is the notification's, so the body is its detail
        "message": _cut(body(one), 3800) if one else plain(events),
        "priority": int(_PRIORITY[sev]),
        "tags": [_TAGS[sev]],
    }
    if one and (url := link(one, app_url)):
        msg["click"] = url
    elif app_url:
        msg["click"] = f"{app_url}/alerts"
    headers = {}
    auth: httpx.Auth = httpx.Auth()  # none
    if token := secrets.get("token"):
        headers["authorization"] = f"Bearer {token}"
    elif config.get("username"):
        auth = httpx.BasicAuth(config["username"], secrets.get("password", ""))
    await _post(http, config["server"].rstrip("/"), json=msg, headers=headers, auth=auth)


async def telegram_chats(http: httpx.AsyncClient, token: str) -> list[dict[str, Any]]:
    """Chats the bot has seen lately (someone wrote to it, or it was added to a group), so
    a chat ID needn't be looked up by hand. Telegram keeps updates for 24 hours."""
    url = f"{TELEGRAM_API}/bot{quote(token, safe=':')}/getUpdates"
    data = await _request(http, "GET", url, 2**20)
    try:
        result = json.loads(data).get("result", [])
    except ValueError as e:
        raise NotifyError("Telegram's answer wasn't readable") from e
    chats: dict[int, dict[str, Any]] = {}
    for u in result:
        for key in ("message", "channel_post", "my_chat_member", "edited_message"):
            chat = (u.get(key) or {}).get("chat")
            if chat and "id" in chat:
                name = chat.get("title") or " ".join(
                    x for x in (chat.get("first_name"), chat.get("last_name")) if x
                )
                chats[chat["id"]] = {
                    "id": str(chat["id"]),
                    "type": chat.get("type", ""),
                    "name": name or chat.get("username", ""),
                }
    return list(chats.values())
