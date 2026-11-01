"""What every connector other than Google declares (BD-06; NS-23.1-4, with NS-23.1-3/-6/-7).

The Google connector's contract (services/connector_contracts.py) says every host it calls, what
Google receives, what is kept on this PC and for how long, its limits and what removing it does.
No other connector said any of that. This module says it for every other connector Friday lists:

* the tool-server connectors in services/connectors.CONNECTOR_DEFS (Slack, GitHub, Linear,
  Notion, Discord, Windows desktop control, Higgsfield);
* the message channels (services/channels: Telegram, Discord);
* the publishing platforms (services/platforms).

Each declaration is checked against the code by tests/api/test_every_connector_declares.py: a host
an isolated server is allowed to reach, a remote server's address, the package source its command
fetches from, and every https host in a channel's or a platform's module must be declared; every
place named as kept must be a module that exists and holds the named thing; the call time limit
is the client's own. A declaration that drifts from the code fails there, not in front of the owner.

`contract(key)` is the declaration with its card (the lines the Connections page shows); Google's
comes from connector_contracts. Nothing here grants or refuses anything: the isolation, the egress
gate and the approval cards do that. This only says, truthfully, what they let through.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

#: services/mcp_client._DEFAULT_CALL_TIMEOUT (the test holds them equal)
_MCP_CALL_TIMEOUT_S = 120.0


def _r(what: str, where: str, kept: str, module: str, symbol: str) -> Dict[str, str]:
    return {"what": what, "where": where, "kept": kept, "module": module, "symbol": symbol}


# ── what every tool-server call leaves on this PC ─────────────────────────────
_MCP_COMMON_RETENTION = [
    _r("a line for each tool call: the server, the tool, the names of the arguments (not their "
       "values), how long the answer was and how long it took",
       "security/mcp_audit.log", "kept", "services/extension_security.py", "params_keys"),
    _r("a record of each call that left (where it went, when, a fingerprint; the arguments "
       "encrypted)", "the egress log", "kept", "services/egress_gate.py", "record_service_send"),
    _r("a signed record of each action Friday was allowed or refused",
       "the action ledger", "kept; moved to an archive after 730 days (Settings)",
       "services/action_ledger.py", "ledger_archive_after_days"),
    _r("what the service answered, as part of the conversation that asked",
       "that conversation", "until you delete the conversation", "mcp_client.py",
       "_flatten_tool_result"),
]

_LOCAL_TOKEN = _r(
    "the token you pasted", "mcp_servers.json (the server's entry)",
    "encrypted; kept when the connector is disconnected, so reconnecting is one click; replaced "
    "when you connect with a new one", "services/connector_secrets.py", "encrypt_config")

_REMOTE_SIGN_IN = _r(
    "the sign-in (its tokens and Friday's registration with the server)",
    "the credential store (mcp_oauth/<server>)",
    "encrypted, until you remove it under Settings > Privacy & Data > Stored keys",
    "mcp_oauth.py", "def clear_credentials")

_MCP_LIMITS = {
    "call_timeout_s": _MCP_CALL_TIMEOUT_S,
    "service": ("{svc}'s own limits apply. When it asks Friday to slow down the call is reported "
                "as not done, and Friday does not resend it on its own."),
}

_MCP_LIMITATIONS = [
    "The server is a separate program: Friday sees what it answers, not what it does with the "
    "token in between.",
    "A call the server did not confirm (a timeout, an error after the request went out, a "
    "malformed answer) is reported as not done; it may still have happened at {svc}.",
]

_LOCAL_REVOCATION = {
    "how": "Disconnect the connector. Connecting with a new token replaces the old one.",
    "does": ["switches the server off; its tools leave Friday at once"],
    "keeps": ("the token, encrypted, so reconnecting is one click. Friday has no button that "
              "deletes a connector's token yet: cancel it at {svc} (below) if it may have been "
              "seen."),
}

_REMOTE_REVOCATION = {
    "how": "Remove its sign-in under Settings > Privacy & Data > Stored keys.",
    "does": ["deletes Friday's copy of the sign-in; the server asks you to sign in again"],
    "keeps": "the records above; nothing that would let Friday sign in again",
}


def _tool_server(key: str, name: str, *, local: bool, destinations: List[Dict[str, str]],
                 receives: List[str], at_service: str, limitations: List[str]) -> Dict[str, Any]:
    retention = ([_LOCAL_TOKEN] if local else [_REMOTE_SIGN_IN]) + list(_MCP_COMMON_RETENTION)
    rev = dict(_LOCAL_REVOCATION if local else _REMOTE_REVOCATION)
    rev = {k: (v.format(svc=name) if isinstance(v, str) else list(v)) for k, v in rev.items()}
    rev["at_service"] = at_service
    return {
        "connector": key, "name": name,
        "kind": "tool server (runs on this PC)" if local else "tool server (remote)",
        "destinations": destinations, "receives": receives, "retention": retention,
        "rate_limits": {k: (v.format(svc=name) if isinstance(v, str) else v)
                        for k, v in _MCP_LIMITS.items()},
        "revocation": rev,
        "limitations": [s.format(svc=name) for s in _MCP_LIMITATIONS] + list(limitations),
    }


_NPM = {"host": "registry.npmjs.org", "for": "fetching the server program (npx) when it starts"}

TOOL_SERVERS: Dict[str, Dict[str, Any]] = {
    "slack": _tool_server(
        "slack", "Slack", local=True,
        destinations=[_NPM, {"host": "slack.com", "for": "Slack's API"},
                      {"host": "*.slack.com", "for": "Slack's API and files"}],
        receives=["the bot token, with every request",
                  "the arguments of each Slack tool Friday calls: channel names, search words, "
                  "and the text of any message it posts"],
        at_service="Revoke the bot token in your Slack app's settings (api.slack.com/apps > "
                   "OAuth & Permissions).",
        limitations=["Runs in Windows' isolated mode where available: it reaches the internet "
                     "and its own folder, not your files."]),
    "github": _tool_server(
        "github", "GitHub", local=True,
        destinations=[{"host": "ghcr.io", "for": "fetching the server image (docker) when it starts"},
                      {"host": "api.github.com", "for": "GitHub's API"}],
        receives=["the personal access token, with every request",
                  "the arguments of each GitHub tool Friday calls: repository names, search "
                  "words, and the text of any issue, comment or file it writes"],
        at_service="Delete the token at github.com/settings/tokens.",
        limitations=["Runs in Docker, not in Windows' isolated mode: Friday's network allowlist "
                     "is not applied to it."]),
    "linear": _tool_server(
        "linear", "Linear", local=False,
        destinations=[{"host": "mcp.linear.app", "for": "Linear's tool server"}],
        receives=["the sign-in token, with every request",
                  "the arguments of each Linear tool Friday calls: issue titles, descriptions, "
                  "comments and search words"],
        at_service="Remove Friday from Linear's authorized applications (Linear > Settings > "
                   "Account > Security & access).",
        limitations=["Signing in happens in your browser, at Linear."]),
    "notion": _tool_server(
        "notion", "Notion", local=False,
        destinations=[{"host": "mcp.notion.com", "for": "Notion's tool server"}],
        receives=["the sign-in token, with every request",
                  "the arguments of each Notion tool Friday calls: page and database names, "
                  "search words, and the text of any page it writes"],
        at_service="Disconnect Friday under Notion > Settings > Connections.",
        limitations=["Signing in happens in your browser, at Notion."]),
    "discord": _tool_server(
        "discord", "Discord", local=True,
        destinations=[_NPM, {"host": "discord.com", "for": "Discord's API"},
                      {"host": "*.discord.com", "for": "Discord's API"},
                      {"host": "gateway.discord.gg", "for": "Discord's live connection"},
                      {"host": "*.discord.gg", "for": "Discord's live connection"},
                      {"host": "cdn.discordapp.com", "for": "attachments and images"}],
        receives=["the bot token, with every request",
                  "the arguments of each Discord tool Friday calls: server and channel names, "
                  "and the text of any message it posts"],
        at_service="Reset the bot token in the Discord Developer Portal (Applications > Bot).",
        limitations=["Runs in Windows' isolated mode where available: it reaches the internet "
                     "and its own folder, not your files."]),
    "windows_desktop": _tool_server(
        "windows_desktop", "Windows desktop control", local=True,
        destinations=[{"host": "pypi.org", "for": "fetching the server program (uvx) when it starts"},
                      {"host": "files.pythonhosted.org", "for": "the server program's files"}],
        receives=["nothing leaves this PC: it reads the screen and the window list, and acts on "
                  "apps here"],
        at_service="Nothing to revoke: it has no account.",
        limitations=["Not isolated: it acts on this PC's screen, which is why every press it makes "
                     "is checked again just before it acts.",
                     "Only its read tools are switched on until you switch others on."]),
    "higgsfield": _tool_server(
        "higgsfield", "Higgsfield", local=False,
        destinations=[{"host": "mcp.higgsfield.ai", "for": "Higgsfield's tool server"}],
        receives=["the sign-in token, with every request",
                  "the prompt and settings of each image or video you ask it to make"],
        at_service="Sign out of Friday's session in your Higgsfield account settings.",
        limitations=["Each generation spends Higgsfield credits; Friday reports what it cost.",
                     "Finished files are fetched from the address Higgsfield's answer names, "
                     "which is not listed above."]),
}


# ── message channels ──────────────────────────────────────────────────────────

def _channel(key: str, name: str, destinations: List[Dict[str, str]], at_service: str,
             extra_retention: List[Dict[str, str]]) -> Dict[str, Any]:
    return {
        "connector": key, "name": name, "kind": "message channel",
        "destinations": destinations,
        "receives": ["the bot token, with every request",
                     "each reply Friday sends to a chat on the channel's allowlist, after the "
                     "egress gate checked it (a chat not on the list gets no reply)"],
        "retention": [
            _r("the bot token", "the credential store (channel_%s)" % name.lower(),
               "encrypted, until you remove it under Settings > Privacy & Data > Stored keys",
               "services/channels/base.py", "get_provider_key"),
        ] + list(extra_retention) + [
            _r("a record of each message sent (where, when, a fingerprint; the text encrypted)",
               "the egress log", "kept", "services/channels/base.py", "record_service_send"),
            _r("a signed record of each send Friday was allowed or refused", "the action ledger",
               "kept; moved to an archive after 730 days (Settings)",
               "services/action_ledger.py", "ledger_archive_after_days"),
        ],
        "rate_limits": {"service": "%s's own limits apply; a send it refuses is reported as not "
                                   "sent." % name},
        "revocation": {
            "how": "Remove its bot token under Settings > Privacy & Data > Stored keys.",
            "does": ["the channel stops: no token, nothing is read or sent"],
            "keeps": "the records above",
            "at_service": at_service,
        },
        "limitations": ["A message the service did not confirm is reported as not confirmed, "
                        "never as delivered.",
                        "When the service refuses the token, the channel says so until the "
                        "token is replaced."],
    }


CHANNELS: Dict[str, Dict[str, Any]] = {
    "channel_telegram": _channel(
        "channel_telegram", "Telegram",
        [{"host": "api.telegram.org", "for": "Telegram's bot API (reading and sending)"}],
        "Revoke the token with @BotFather (/revoke).",
        [_r("where Telegram's message list was read up to (a short hash of the token and a "
            "number; no message text)", "channels/telegram-offset.json", "until replaced",
            "services/channels/telegram_bridge.py", "telegram-offset.json")]),
    "channel_discord": _channel(
        "channel_discord", "Discord",
        [{"host": "discord.com", "for": "Discord's API (sending)"},
         {"host": "gateway.discord.gg", "for": "Discord's live connection (reading)"}],
        "Reset the bot token in the Discord Developer Portal (Applications > Bot).", []),
}


# ── publishing platforms ──────────────────────────────────────────────────────

#: The hosts each platform's module calls (every https host in it; the test checks).
_PLATFORM_HOSTS: Dict[str, List[str]] = {
    "bluesky": ["bsky.social", "bsky.app"],
    "instagram": ["graph.instagram.com", "api.instagram.com", "rupload.facebook.com",
                  "www.instagram.com"],
    "linkedin": ["api.linkedin.com", "www.linkedin.com"],
    "medium": ["api.medium.com", "medium.com"],
    "reddit": ["oauth.reddit.com", "www.reddit.com"],
    "tiktok": ["open.tiktokapis.com", "www.tiktok.com"],
    "twitter": ["api.x.com", "x.com"],
    "youtube": ["www.googleapis.com", "youtubeanalytics.googleapis.com", "www.youtube.com"],
    # the owner names these when connecting: their own Mastodon server, their newsletter
    "mastodon": ["the Mastodon server you connect"],
    "substack": ["*.substack.com"],
}

_PLATFORM_NAMES = {"twitter": "X", "linkedin": "LinkedIn", "youtube": "YouTube",
                   "tiktok": "TikTok"}


def _platform(name: str) -> Dict[str, Any]:
    label = _PLATFORM_NAMES.get(name, name.title())
    return {
        "connector": name, "name": label, "kind": "publishing platform",
        "destinations": [{"host": h, "for": "%s's API and sign-in" % label}
                         for h in _PLATFORM_HOSTS.get(name, [])],
        "receives": ["the account's sign-in or key, with every request",
                     "each post Friday publishes: its text, images or video",
                     "requests for your own posts and their numbers, where the platform "
                     "offers them"],
        "retention": [
            _r("the account's sign-in", "platforms/%s.cred" % name,
               "encrypted, until you disconnect the account or remove it under Settings > "
               "Privacy & Data > Stored keys", "services/platforms/base.py", "_cred_path"),
            _r("how many posts and calls were made today (to stay inside the platform's limits)",
               "platforms/rate_budget.json", "until replaced", "services/platforms/base.py",
               "rate_budget.json"),
            _r("posts, drafts and their schedule", "content/", "until you delete them",
               "services/content_pipeline.py", "CONTENT_DIR"),
            _r("a record of each post sent (where, when, a fingerprint; the text encrypted)",
               "the egress log", "kept", "services/egress_gate.py", "record_service_send"),
        ],
        "rate_limits": {"service": "Friday keeps its own daily count per platform to stay inside "
                                   "the platform's limits; the platform's limits still apply."},
        "revocation": {
            "how": "Remove its sign-in under Settings > Privacy & Data > Stored keys (or "
                   "disconnect it in Content > Accounts).",
            "does": ["from Stored keys: asks %s to revoke the sign-in where it allows that" % label,
                     "deletes Friday's copy of the sign-in and key"],
            "keeps": "posts and drafts, and the records above",
            "at_service": "Check %s's connected apps page and remove Friday there too." % label,
        },
        "limitations": ["Whether %s confirmed a revocation is not always known." % label],
    }


def _platform_names() -> List[str]:
    try:
        from agent_friday.services.connector_adapters import _HIDDEN_PLATFORMS
        from agent_friday.services import platforms as _p
        names = [n for n in getattr(_p, "ADAPTER_MODULES", {}).values()]
    except Exception:  # noqa: BLE001
        return list(_PLATFORM_HOSTS)
    out: List[str] = []
    for n in names:
        if n and n not in _HIDDEN_PLATFORMS and n not in out:
            out.append(n)
    return out


def declaration(key: str) -> Optional[Dict[str, Any]]:
    """The declaration for a connector key (not Google: see connector_contracts), or None."""
    key = str(key or "")
    if key in TOOL_SERVERS:
        return TOOL_SERVERS[key]
    if key in CHANNELS:
        return CHANNELS[key]
    if key in _platform_names():
        return _platform(key)
    return None


def keys() -> List[str]:
    return list(TOOL_SERVERS) + list(CHANNELS) + _platform_names()


def card(decl: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The lines the Connections page shows for one connector."""
    name = decl["name"]
    rl = decl.get("rate_limits") or {}
    limits = [rl["service"]] if rl.get("service") else []
    if rl.get("call_timeout_s"):
        limits.append("Friday waits up to %d seconds for an answer to each call." % int(rl["call_timeout_s"]))
    rev = decl.get("revocation") or {}
    return [
        {"heading": "Where it connects",
         "lines": ["%s: %s" % (d["host"], d["for"]) for d in decl.get("destinations") or []]},
        {"heading": "What %s receives" % name, "lines": list(decl.get("receives") or [])},
        {"heading": "Kept on this PC",
         "lines": ["%s (%s): %s" % (r["what"], r["where"], r["kept"]) for r in decl.get("retention") or []]},
        {"heading": "Limits", "lines": limits + list(decl.get("limitations") or [])},
        {"heading": "Removing it",
         "lines": [x for x in [rev.get("how")] + list(rev.get("does") or []) +
                   (["Kept: " + rev["keeps"]] if rev.get("keeps") else []) +
                   ([rev["at_service"]] if rev.get("at_service") else []) if x]},
    ]


def contract(key: str) -> Optional[Dict[str, Any]]:
    """The declaration with its card, for any connector Friday lists (Google's from
    connector_contracts), or None for a key Friday does not know."""
    if str(key or "") == "google":
        from agent_friday.services import connector_contracts as cc
        return cc.google_contract()
    decl = declaration(key)
    if decl is None:
        return None
    return dict(decl, card=card(decl))
