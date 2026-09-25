"""The "connect everything" checklist: every service, what it asks for, and
whether it is connected, read from the sources that already know.

``GET /api/setup/connections`` serves ``checklist()``. It is an aggregate, not
a new store: statuses come from the credential store, the provider registry,
the Google account index, the connector registry, the publishing-platform
adapters, the channel manager and the phone config. The only state this module
adds is which items the user chose to skip, held in the setup chat's state.

WHAT IT NEVER CONTAINS. No secret value, masked or not. A secure field on a
card posts straight to the existing credential endpoint named in its
``connect`` block (for example ``POST /api/providers/<name>/key``); the value
never passes through the setup chat, its transcript, its logs or a model.

WHAT FRIDAY NEVER DOES TO CONNECT SOMETHING. It never reads a browser's
password store or cookies, never opens another application's credential
files, and never asks for a password in the chat. Every connection is either
the vendor's own OAuth consent screen or a key the user pastes into a secure
field on the card.

Each group is built separately, so a mechanism that fails to load costs its
own rows and is reported as such, never the whole list.
"""
from __future__ import annotations

import logging
import sys

from agent_friday.services import setup_chat_copy as copy

_log = logging.getLogger("friday.setup_connections")

GROUPS = (
    ("ai", "AI models"),
    ("google", "Google account"),
    ("phone", "Phone"),
    ("developer", "Developer"),
    ("social", "Social and publishing"),
    ("messaging", "Messaging"),
    ("tools", "Tools"),
    ("search", "Web search"),
    ("voice", "Voice"),
    ("network", "Network"),
)

CONNECTED = "connected"
ATTENTION = "needs_attention"
NOT_CONNECTED = "not_connected"
SKIPPED = "skipped"
EXTERNAL = "external"

#: Provider order in the AI group: Anthropic, then OpenRouter as the one-key
#: alternative (services/one_key.py), then the ones people ask for next.
_PROVIDER_FIRST = ("anthropic", "openrouter", "openai", "google-gemini", "huggingface")
#: The two providers either of which is enough on its own.
_ONE_KEY = ("anthropic", "openrouter")
#: Registry entries that are not an account someone connects with a key.
_PROVIDER_EXCLUDE = ("custom",)


def _item(id, group, label, *, unlocks, permissions, connect, status,
          detail="", docs_url=""):
    return {"id": id, "group": group, "label": label, "unlocks": unlocks,
            "permissions": permissions, "connect": connect, "status": status,
            "detail": detail, "docs_url": docs_url}


def _perm(text, *, scope="", optional=False, key="", default=False):
    p = {"text": text}
    if scope:
        p["scope"] = scope
    if optional:
        p.update({"optional": True, "key": key, "default": bool(default)})
    return p


def _health_status(state: str) -> str:
    return {"working": CONNECTED, "degraded": ATTENTION, "needs_user": ATTENTION,
            "unreadable": ATTENTION}.get(state or "", NOT_CONNECTED)


def _connector_status(status: str) -> str:
    return {"connected": CONNECTED, "error": ATTENTION,
            "connecting": ATTENTION}.get(status or "", NOT_CONNECTED)


# ── AI models ────────────────────────────────────────────────────────────────

def _provider_status(name: str, reg) -> tuple[str, str]:
    from agent_friday.services import credential_store as cs
    st = cs.provider_key_status(name)
    if st == "connected":
        return CONNECTED, "Key stored encrypted on this computer."
    if st == "present_but_unreadable":
        return ATTENTION, "A key is stored but can't be opened; paste it again."
    try:
        if reg is not None and reg.is_provider_available(name):
            return CONNECTED, "Key found in the environment."
    except Exception:
        pass
    return NOT_CONNECTED, ""


def _ai_items() -> list:
    from agent_friday.services.provider_registry import get_provider_registry
    from agent_friday.routing.provider_descriptors import SIGNUP_URLS
    reg = get_provider_registry()
    rows = []
    for p in reg.list_providers():
        name = p.get("name") or ""
        auth = p.get("auth") or {}
        if name in _PROVIDER_EXCLUDE or auth.get("type") != "env_var":
            continue
        rows.append(p)
    rows.sort(key=lambda p: (_PROVIDER_FIRST.index(p["name"])
                             if p["name"] in _PROVIDER_FIRST else 99,
                             (p.get("label") or p["name"]).lower()))
    try:
        from agent_friday.services import one_key
        in_use = one_key.key_in_use()
    except Exception:
        in_use = None
    out = []
    for p in rows:
        name, label = p["name"], p.get("label") or p["name"]
        status, detail = _provider_status(name, reg)
        connect = {"kind": "secret",
                   "fields": [{"key": "key", "label": "API key", "secret": True}],
                   "endpoint": "/api/providers/%s/key" % name,
                   "test": "/api/providers/%s/test" % name,
                   "signup_url": SIGNUP_URLS.get(name, "")}
        if name in _ONE_KEY:
            # Saving either key is followed by a one-token check that says
            # whether Friday can think now.
            connect["verify"] = "/api/setup/verify-key/%s" % name
            if name == in_use and status == CONNECTED:
                detail = (detail + " " if detail else "") + copy.ONE_KEY_IN_USE.format(
                    label=one_key.LABELS[name])
        out.append(_item(
            "provider:" + name, "ai", label,
            unlocks=copy.PROVIDER_UNLOCKS.get(
                name, "Chat and reasoning with %s models." % label),
            permissions=[_perm(copy.PROVIDER_PERMISSION.format(label=label))],
            connect=connect, status=status, detail=detail))
    # The local option: no key, no account.
    local_ok, local_detail = False, ""
    try:
        from agent_friday.services import local_seats
        rows_local = local_seats.installed()
        local_ok = bool(rows_local)
        local_detail = ("%d model(s) installed on this computer." % len(rows_local)
                        if rows_local else "No local model installed yet.")
    except Exception:
        local_detail = "Could not check for local models."
    out.insert(0, _item(
        "local", "ai", "On this computer (local models)",
        unlocks=copy.UNLOCKS["local"],
        permissions=[_perm("Nothing leaves this computer. Needs a graphics card "
                           "with a few GB free, or a lot of patience on a CPU.")],
        connect={"kind": "local", "hint": "See the hardware card, or Settings > Models."},
        status=CONNECTED if local_ok else NOT_CONNECTED, detail=local_detail))
    return out


# ── Google ───────────────────────────────────────────────────────────────────

def _google_items() -> list:
    from agent_friday.services import google_accounts as ga
    perms = [_perm(copy.GOOGLE_SCOPE_WORDS.get(sc, sc), scope=sc)
             for sc in ga.GOOGLE_MULTI_SCOPES]
    perms.append(_perm(copy.GOOGLE_SCOPE_WORDS[ga.GMAIL_SEND], scope=ga.GMAIL_SEND,
                       optional=True, key="include_send"))
    perms.append(_perm(copy.GOOGLE_SCOPE_WORDS[ga.GMAIL_MODIFY], scope=ga.GMAIL_MODIFY,
                       optional=True, key="include_modify"))
    try:
        summ = ga.accounts_summary()
    except Exception:
        summ = {"total": 0, "healthy": 0}
    total, healthy = int(summ.get("total") or 0), int(summ.get("healthy") or 0)
    if healthy:
        status = CONNECTED
        detail = "%d account%s connected." % (healthy, "" if healthy == 1 else "s")
        if total > healthy:
            status, detail = ATTENTION, detail + " %d need%s reconnecting." % (
                total - healthy, "s" if total - healthy == 1 else "")
    elif total:
        status, detail = ATTENTION, "Every connected account needs reconnecting."
    else:
        status, detail = NOT_CONNECTED, ""
    return [_item(
        "google", "google", "Google: Gmail, Calendar, Drive, Contacts, Tasks",
        unlocks=copy.UNLOCKS["google"], permissions=perms,
        connect={"kind": "google_oauth", "endpoint": "/api/google/accounts/connect",
                 "multi_account": True,
                 "note": "Google's own consent screen opens in your browser and "
                         "lists the same permissions. Sending and mailbox "
                         "changes are asked for only if you tick them."},
        status=status, detail=detail,
        docs_url="https://myaccount.google.com/permissions")]


# ── Phone ────────────────────────────────────────────────────────────────────

def _phone_items() -> list:
    from agent_friday.phone import config as pc
    cfg = pc.load()
    secrets = {n: pc.secret_status(n) for n in pc.SECRET_NAMES}
    have = [bool(cfg.get("account_sid")), bool(cfg.get("api_key_sid")),
            secrets.get("api_key_secret") == "stored",
            secrets.get("auth_token") == "stored"]
    if all(have) and cfg.get("owner_cell_verified"):
        status, detail = CONNECTED, "Configured and your cell is verified."
    elif any(have):
        status, detail = ATTENTION, ("Partly set up. Finish in Settings > "
                                     "Accounts & Keys > Phone (number, "
                                     "verification, public address).")
    else:
        status, detail = NOT_CONNECTED, ""
    if any(v == "unreadable" for v in secrets.values()):
        status, detail = ATTENTION, "A stored Twilio secret can't be opened; paste it again."
    fields = [
        {"key": "account_sid", "label": "Account SID (AC...)", "secret": False,
         "endpoint": "/api/phone/config", "shape": "patch", "set": have[0]},
        {"key": "api_key_sid", "label": "API key SID (SK...)", "secret": False,
         "endpoint": "/api/phone/config", "shape": "patch", "set": have[1]},
        {"key": "api_key_secret", "label": "API key secret", "secret": True,
         "endpoint": "/api/phone/secret", "shape": "name_value", "set": have[2]},
        {"key": "auth_token", "label": "Account auth token", "secret": True,
         "endpoint": "/api/phone/secret", "shape": "name_value", "set": have[3]},
    ]
    return [_item(
        "twilio", "phone", "Phone (Twilio)", unlocks=copy.UNLOCKS["twilio"],
        permissions=[
            _perm("Answer calls to your Twilio number and take voicemail."),
            _perm("Text or call only your own verified cell, and only messages "
                  "you approve on a card."),
            _perm("Twilio holds message bodies and recordings until Friday asks "
                  "it to delete them (on by default)."),
        ],
        connect={"kind": "fields", "fields": fields,
                 "more": "Settings > Accounts & Keys > Phone"},
        status=status, detail=detail, docs_url="https://console.twilio.com")]


# ── MCP connectors (developer, messaging, tools) ─────────────────────────────

_CONNECTOR_GROUP = {"github": "developer", "slack": "messaging",
                    "discord": "messaging", "linear": "tools", "notion": "tools",
                    "higgsfield": "tools"}

_CONNECTOR_PERMS = {
    "github": ("Whatever your personal access token grants. Friday asks for "
               "two scopes: repo (read and write code, issues and pull "
               "requests) and read:org (read your organisations).",),
    "slack": ("The bot scopes you give your Slack app, typically: read "
              "channels and history, search messages, post messages.",
              "Posts still wait for your approval card."),
    "discord": ("Whatever your bot is allowed in the servers you invite it to: "
                "typically read messages and post messages.",
                "Posts still wait for your approval card."),
    "linear": ("Access you approve on Linear's own consent screen: your "
               "workspace's issues, projects and cycles.",),
    "notion": ("Only the pages and databases you pick on Notion's own consent "
               "screen.",),
    "higgsfield": ("Access you approve on Higgsfield's consent screen: your "
                   "account's generation tools and credit balance.",
                   "Paid generations still ask before they run."),
}


def _connector_items() -> list:
    from agent_friday.services import connectors as C
    out = []
    for key in ("github", "slack", "discord", "linear", "notion", "higgsfield"):
        d = C.CONNECTOR_DEFS.get(key)
        if not d:
            continue
        st = C._build_status(key) or {}
        fields = [{"key": f["key"], "label": f.get("label") or f["key"],
                   "secret": bool(f.get("secret")), "set": bool(f.get("set"))}
                  for f in (st.get("fields") or [])]
        oauth = bool((d.get("mcp_template") or {}).get("url"))
        label = d.get("name") or key
        if key == "discord":
            label = "Discord servers (read and post)"
        out.append(_item(
            "connector:" + key, _CONNECTOR_GROUP[key], label,
            unlocks=copy.UNLOCKS.get("connector:" + key, d.get("blurb") or ""),
            permissions=[_perm(t) for t in _CONNECTOR_PERMS.get(key, ())],
            connect=({"kind": "oauth_connector",
                      "endpoint": "/api/connectors/%s/connect" % key}
                     if oauth else
                     {"kind": "fields", "fields": fields,
                      "endpoint": "/api/connectors/%s/connect" % key,
                      "shape": "flat"}),
            status=_connector_status(st.get("status")),
            detail=st.get("detail") or "", docs_url=d.get("docs_url") or ""))
    return out


# ── Messaging bridges (talk to Friday from another app) ──────────────────────

def _channel_items() -> list:
    from agent_friday.services import connector_adapters as ca
    out = []
    for conn in ca.channel_connectors():
        name = conn.id.replace("channel_", "", 1)
        try:
            h = conn.health()
            status = _health_status(h.state)
            detail = h.summary or ""
        except Exception:
            status, detail = NOT_CONNECTED, ""
        out.append(_item(
            "channel:" + name, "messaging",
            "%s (talk to Friday)" % conn.label,
            unlocks=copy.UNLOCKS.get("channel:" + name, conn.blurb),
            permissions=[
                _perm("Messages you send the bot reach Friday; only accounts on "
                      "the allowlist get an answer."),
                _perm("Friday replies through the bot. Anything outward it "
                      "would do still asks you first."),
            ],
            connect={"kind": "fields", "shape": "channel",
                     "endpoint": "/api/channels/%s/configure" % name,
                     "fields": [{"key": "token", "label": "Bot token",
                                 "secret": True, "set": status != NOT_CONNECTED}]},
            status=status, detail=detail))
    return out


# ── Social and publishing ────────────────────────────────────────────────────

def _platform_scopes(adapter) -> list:
    mod = sys.modules.get(type(adapter).__module__)
    raw = None
    for attr in ("YOUTUBE_SCOPES", "SCOPES", "_SCOPES"):
        if mod is not None and getattr(mod, attr, None):
            raw = getattr(mod, attr)
            break
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = [p for p in raw.replace(",", " ").split() if p]
    else:
        parts = [str(p) for p in raw]
    return parts


def _platform_items() -> list:
    from agent_friday.services import connector_adapters as ca
    out = []
    for conn in ca.platform_connectors():
        adapter = getattr(conn, "_adapter", None)
        mode = getattr(adapter, "auth_mode", "manual") if adapter else "manual"
        scopes = _platform_scopes(adapter) if adapter else []
        perms = [_perm(copy.PLATFORM_SCOPE_WORDS.get(s)
                       or copy.GOOGLE_SCOPE_WORDS.get(s) or s, scope=s)
                 for s in scopes]
        if not perms and mode == "manual":
            perms = [_perm("No API access: Friday prepares posts and you publish "
                           "them yourself.")]
        perms.append(_perm("Every post still waits for your approval card."))
        try:
            h = conn.health()
            status, detail = _health_status(h.state), h.summary or ""
        except Exception:
            status, detail = NOT_CONNECTED, ""
        if mode in ("oauth2", "oauth2_pkce"):
            connect = {"kind": "platform_oauth",
                       "endpoint": "/api/content/platforms/%s/connect" % conn.id}
        elif mode in ("token", "app_password"):
            fields = []
            if conn.id == "bluesky":
                fields.append({"key": "identifier", "label": "Handle (you.bsky.social)",
                               "secret": False})
            if conn.id == "mastodon":
                fields.append({"key": "instance", "label": "Server (mastodon.social)",
                               "secret": False})
            fields.append({"key": "secret",
                           "label": ("App password" if mode == "app_password"
                                     else "Access token"),
                           "secret": True})
            connect = {"kind": "fields", "shape": "flat", "fields": fields,
                       "endpoint": "/api/content/platforms/%s/connect" % conn.id}
        else:
            connect = {"kind": "manual",
                       "hint": "Nothing to connect: Friday drafts, you post."}
        out.append(_item("platform:" + conn.id, "social", conn.label,
                         unlocks="Draft, schedule and publish to %s, with your approval."
                                 % conn.label,
                         permissions=perms, connect=connect, status=status,
                         detail=detail))
    return out


# ── Search and voice keys ────────────────────────────────────────────────────

def _key_status(name: str, env_key: str) -> tuple[str, str]:
    import os
    from agent_friday.services import credential_store as cs
    st = cs.provider_key_status(name)
    if st == "connected":
        return CONNECTED, "Key stored encrypted on this computer."
    if st == "present_but_unreadable":
        return ATTENTION, "A key is stored but can't be opened; paste it again."
    if env_key and (os.environ.get(env_key) or "").strip():
        return CONNECTED, "Key found in the environment."
    return NOT_CONNECTED, ""


def _secret_connect(name: str, signup: str = "") -> dict:
    return {"kind": "secret", "endpoint": "/api/providers/%s/key" % name,
            "fields": [{"key": "key", "label": "API key", "secret": True}],
            "signup_url": signup}


def _search_items() -> list:
    out = []
    for name, label, env_key, signup, perm in (
            ("brave", "Brave Search", "BRAVE_API_KEY",
             "https://api.search.brave.com/app/keys",
             "Your search queries are sent to Brave with your key."),
            ("firecrawl", "Firecrawl", "FIRECRAWL_API_KEY",
             "https://www.firecrawl.dev/app/api-keys",
             "Your search queries and the pages Friday reads are fetched "
             "through Firecrawl with your key.")):
        status, detail = _key_status(name, env_key)
        out.append(_item("provider:" + name, "search", label,
                         unlocks=copy.UNLOCKS["provider:" + name],
                         permissions=[_perm(perm)],
                         connect=_secret_connect(name, signup),
                         status=status, detail=detail))
    return out


def _voice_items() -> list:
    out = []
    try:
        from agent_friday.services.cloud_voice import PROVIDERS as VP
    except Exception:
        VP = {}
    for name, label, env_key, signup in (
            ("elevenlabs", "ElevenLabs", "ELEVENLABS_API_KEY",
             "https://elevenlabs.io/app/settings/api-keys"),
            ("inworld", "Inworld", "INWORLD_API_KEY", "https://platform.inworld.ai")):
        status, detail = _key_status(name, env_key)
        perms = [_perm("Text you choose to have spoken in a %s voice is sent to %s."
                       % (label, label))]
        retention = (VP.get(name) or {}).get("retention")
        if retention:
            perms.append(_perm(retention))
        out.append(_item("provider:" + name, "voice", label,
                         unlocks=copy.UNLOCKS["provider:" + name],
                         permissions=perms, connect=_secret_connect(name, signup),
                         status=status, detail=detail))
    return out


def _network_items() -> list:
    return [_item(
        "cloudflare", "network", "Cloudflare (tunnels)",
        unlocks=copy.UNLOCKS["cloudflare"],
        permissions=[_perm("Friday holds no Cloudflare credential and asks for none.")],
        connect={"kind": "external", "steps": list(copy.CLOUDFLARE_STEPS)},
        status=EXTERNAL, detail="Set up outside Friday.",
        docs_url="https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/")]


_BUILDERS = (
    ("ai", _ai_items), ("google", _google_items), ("phone", _phone_items),
    ("connectors", _connector_items), ("social", _platform_items),
    ("messaging", _channel_items), ("search", _search_items),
    ("voice", _voice_items), ("network", _network_items),
)


def checklist(skipped=()) -> dict:
    """The whole checklist, grouped, with statuses. Never a secret value."""
    skipped = set(skipped or ())
    items, errors = [], []
    for name, build in _BUILDERS:
        try:
            items.extend(build())
        except Exception as e:
            _log.warning("setup checklist: %s could not be read: %s", name, e)
            errors.append({"source": name, "error": "%s: %s" % (type(e).__name__, e)})
    for it in items:
        if it["id"] in skipped and it["status"] == NOT_CONNECTED:
            it["status"] = SKIPPED
    order = {g: i for i, (g, _) in enumerate(GROUPS)}
    items.sort(key=lambda it: order.get(it["group"], 99))
    counts = {}
    for it in items:
        counts[it["status"]] = counts.get(it["status"], 0) + 1
    try:
        from agent_friday.services import one_key
        one = one_key.status()
    except Exception as e:
        _log.warning("setup checklist: one-key status could not be read: %s", e)
        one = None
    return {"groups": [{"id": g, "label": lbl} for g, lbl in GROUPS],
            "items": items, "counts": counts, "errors": errors,
            "one_key": one,
            "never": ("Friday never reads your browser's saved passwords or "
                      "cookies, never opens other apps' credential files, and "
                      "never asks for a password in the chat.")}


def connected_labels() -> list:
    """Labels of everything connected, for the finish summary."""
    try:
        return [it["label"] for it in checklist()["items"]
                if it["status"] == CONNECTED]
    except Exception:
        return []
