"""The existing mechanisms, read through one protocol.

Phase 3 of docs/design/connector-ecosystem.md. Each class here is a THIN
READING of a mechanism that already works. No storage moves, no auth flow
changes, no route is rewritten. What changes is that things which were
invisible become visible: publishing platforms and channel bridges have never
appeared on /api/connectors, in connectors_health, in connected_keys, or in
the connector-down monitor, because services/connectors.py does not import
either package.

Google and MCP are deliberately NOT adapted here. They are already rendered by
`connectors._build_status` with live tool counts and per-field credential
state, and re-deriving them through a second path would create exactly the
disagreement this whole exercise exists to remove. They join the protocol when
their own surfaces move onto it, not before.
"""
from __future__ import annotations

from agent_friday.services import connector_health as _ch
from agent_friday.services.connector_protocol import (
    AccountRef, AuthField, AuthSpec, Connector,
)

#: Publishing platforms that exist only for tests, or that are local machinery
#: rather than an account the user connects. Hidden so the connectors page does
#: not fill with rows nobody asked for.
#:
#: Matched against the CANONICAL id (the VALUE in ADAPTER_MODULES), not the
#: module name - `federation_pub` is the module and `federation` is the id, and
#: naming the module here silently hid nothing.
_HIDDEN_PLATFORMS = {"mock", "federation"}

_PLATFORM_ICONS = {
    "linkedin": "💼", "x_twitter": "𝕏", "bluesky": "🦋", "mastodon": "🐘",
    "instagram": "📸", "youtube": "▶️", "tiktok": "🎵", "reddit": "👽",
    "medium": "✍️", "substack": "📰",
}

_CHANNEL_ICONS = {"telegram": "✈️", "discord": "🎮"}


class PlatformConnector(Connector):
    """One publishing platform (`services/platforms/`).

    The mechanism whose `status()` computed `connected` from whether a
    credential file existed, while storing an `expires_at` it never consulted -
    a token that expired in August reporting as connected in September. That
    derivation was fixed in phase 2; this makes the result visible.
    """

    kind = "platform"
    category = "publishing"

    def __init__(self, name: str):
        self.id = name
        self.icon = _PLATFORM_ICONS.get(name, "📤")
        self._status = {}
        try:
            from agent_friday.services import platforms as _p
            self._adapter = _p.get_adapter(name)
        except Exception:
            self._adapter = None
        self.label = getattr(self._adapter, "label", None) or name.replace("_", " ").title()
        self.blurb = "Publish to %s" % self.label
        self.capabilities = ("publish",)

    def _load(self) -> dict:
        if not self._status:
            try:
                self._status = self._adapter.status() if self._adapter else {}
            except Exception as e:
                self._status = {"_error": "%s: %s" % (type(e).__name__, e)}
        return self._status

    def auth(self) -> AuthSpec:
        mode = (self._load().get("auth_mode") or "none")
        return AuthSpec(mode=mode if mode in
                        ("oauth2", "oauth2_pkce", "app_password", "token",
                         "manual") else "none")

    def accounts(self) -> list:
        s = self._load()
        acct = s.get("account")
        if not acct and not s.get("connected"):
            return []
        return [AccountRef(id=str(acct or self.id), label=str(acct or self.label),
                           health=self.health())]

    def health(self, account_id: str | None = None) -> _ch.Health:
        s = self._load()
        if s.get("_error"):
            return _ch.unknown(detail=s["_error"], source="platforms")
        if self._adapter is None:
            # A platform whose module failed to import is not "disconnected" -
            # nothing was asked of the provider. Tolerant import is the pattern
            # platforms/__init__.py already uses; this reports it honestly.
            return _ch.unknown(
                detail="the %s adapter could not be loaded" % self.id,
                source="platforms")
        h = s.get("health")
        if isinstance(h, dict) and h.get("state") in _ch.STATES:
            return _ch.Health(
                state=h["state"], summary=h.get("summary") or "",
                detail=h.get("detail") or "", action=h.get("action"),
                verified=bool(h.get("verified", True)),
                source=h.get("source") or "platforms",
                source_state=h.get("source_state") or "")
        # An adapter predating the phase-2 change. Presence is all it reports,
        # and presence is not a checked verdict.
        return _ch.from_credential_presence(
            bool(s.get("connected")), "platforms",
            detail=str(s.get("last_error") or ""))


class ChannelConnector(Connector):
    """One messaging bridge (`services/channels/`).

    `/api/channels` has a complete HTTP surface and zero callers in index.html
    (survey 2026-09-19), so Telegram and Discord have been reachable and
    unseeable at the same time. Listing them here is the cheap half of the
    answer to "wire them in or cut them" - you cannot decide about something
    you cannot see.
    """

    kind = "channel"
    category = "messaging"

    def __init__(self, name: str):
        self.id = "channel_%s" % name
        self._name = name
        self.label = name.title()
        self.icon = _CHANNEL_ICONS.get(name, "💬")
        self.blurb = "Talk to Friday over %s" % self.label
        self.capabilities = ("chat",)
        self._status = None

    def _load(self) -> dict:
        if self._status is None:
            try:
                from agent_friday.services.channels import manager as _m
                self._status = (_m.status().get("channels") or {}).get(
                    self._name) or {}
            except Exception as e:
                self._status = {"_error": "%s: %s" % (type(e).__name__, e)}
        return self._status

    def auth(self) -> AuthSpec:
        s = self._load()
        return AuthSpec(mode="token", fields=(
            AuthField(key="BOT_TOKEN", label="%s bot token" % self.label,
                      secret=True, required=True,
                      is_set=bool(s.get("has_token"))),
        ))

    def accounts(self) -> list:
        return []

    def health(self, account_id: str | None = None) -> _ch.Health:
        s = self._load()
        if s.get("_error"):
            return _ch.unknown(detail=s["_error"], source="channels")
        h = s.get("health")
        if isinstance(h, dict) and h.get("state") in _ch.STATES:
            return _ch.Health(
                state=h["state"], summary=h.get("summary") or "",
                detail=h.get("detail") or "", action=h.get("action"),
                verified=bool(h.get("verified", True)),
                source=h.get("source") or "channels",
                source_state=h.get("source_state") or "")
        return _ch.unknown(source="channels",
                           detail="the channel manager reported no health")


def platform_connectors() -> list:
    """Every publishing platform that is worth showing."""
    try:
        from agent_friday.services import platforms as _p
        names = [n for n in getattr(_p, "ADAPTER_MODULES", {}).values()]
    except Exception:
        return []
    seen, out = set(), []
    for n in names:
        if not n or n in seen or n in _HIDDEN_PLATFORMS:
            continue
        seen.add(n)
        try:
            out.append(PlatformConnector(n))
        except Exception:
            # One bad adapter must not cost the other eleven their row.
            continue
    return out


def channel_connectors() -> list:
    try:
        from agent_friday.services.channels import manager as _m
        names = getattr(_m, "CHANNELS", ("telegram", "discord"))
    except Exception:
        return []
    return [ChannelConnector(n) for n in names]


def extra_connectors() -> list:
    """Connectors that `connectors.CONNECTOR_DEFS` does not know about.

    Named "extra" rather than "all" on purpose: Google and MCP are still
    rendered by `connectors._build_status`, and deriving them a second way here
    would recreate the disagreement this work exists to remove.
    """
    return platform_connectors() + channel_connectors()
