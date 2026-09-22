"""What a connector IS.

Phase 3 of docs/design/connector-ecosystem.md. Until now there was no such
type. There was a Google account, and an MCP server, and a platform adapter,
and a channel bridge, and a provider key, and each was written as though it
were the only one. The visible cost: `services/connectors.py` never imported
`services/platforms`, so a LinkedIn connection that died was a connection
nothing watched - absent from /api/connectors, from connectors_health, from
connected_keys, and from the connector-down monitor.

ADAPTERS, NOT REWRITES. Six of the seven mechanisms work. The failure mode of
this project is a rewrite that breaks Google accounts to make a diagram
tidier, so nothing here replaces a storage layer, an auth flow or a route. A
`Connector` is a thin reading of a mechanism that already exists, and
`to_status_dict()` emits the exact shape `connectors.list_connectors()` has
always emitted, so the surfaces consuming it cannot tell the difference except
that more things now appear.

The verdict comes from services/connector_health.py, which is the point: one
vocabulary, derived, failing closed.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from agent_friday.services import connector_health as _ch

#: How a connector is authenticated. Descriptive only - nothing here performs
#: a flow. `oauth2` and `oauth2_pkce` are separated because the difference is
#: visible to the user (a browser round trip either way, but PKCE needs no
#: client secret and therefore no BYO-credentials step).
AUTH_MODES = ("oauth2", "oauth2_pkce", "api_key", "app_password", "token",
              "manual", "none")


@dataclass(frozen=True)
class AuthField:
    """One thing the user has to supply. Never holds the value."""
    key: str
    label: str = ""
    secret: bool = True
    required: bool = True
    placeholder: str = ""
    #: Whether a value is currently stored. A boolean, never the secret - the
    #: existing /api/connectors contract, kept.
    is_set: bool = False

    def as_dict(self) -> dict:
        return {"key": self.key, "label": self.label or self.key,
                "secret": self.secret, "required": self.required,
                "placeholder": self.placeholder, "set": self.is_set}


@dataclass(frozen=True)
class AuthSpec:
    mode: str = "none"
    fields: tuple = ()

    def __post_init__(self):
        if self.mode not in AUTH_MODES:
            object.__setattr__(self, "mode", "none")


@dataclass(frozen=True)
class AccountRef:
    """One identity within a connector.

    EVERY CONNECTOR HAS ACCOUNTS, even the ones that can only have one. Google
    was the only mechanism that supported several, and it was the only one
    whose callers were shaped for it; every other surface hardcoded "the
    credential" as a singular. Expressing the single-account case through the
    same API means adding a second Slack workspace later is a change inside one
    adapter rather than a change to every caller.
    """
    id: str
    label: str = ""
    detail: str = ""
    health: _ch.Health = field(default_factory=_ch.Health)

    def as_dict(self) -> dict:
        return {"id": self.id, "label": self.label or self.id,
                "detail": self.detail, "health": self.health.as_dict()}


class Connector:
    """A thing Friday connects to. Subclasses adapt one existing mechanism."""

    id: str = ""
    label: str = ""
    icon: str = "🔌"
    category: str = "other"
    #: Which mechanism is underneath. Reported so a disagreement can be traced
    #: to its owner rather than argued about.
    kind: str = "other"
    blurb: str = ""
    capabilities: tuple = ()
    workspaces: tuple = ()
    docs_url: str = ""
    setup_hint: str = ""
    multi_account: bool = False

    # ── to implement ────────────────────────────────────────────────────────
    def auth(self) -> AuthSpec:
        return AuthSpec()

    def accounts(self) -> list:
        """Identities inside this connector. May be empty."""
        return []

    def health(self, account_id: str | None = None) -> _ch.Health:
        raise NotImplementedError

    # ── shared ──────────────────────────────────────────────────────────────
    def _safe_health(self, account_id: str | None = None) -> _ch.Health:
        """`health()`, but a broken adapter cannot take down the page whose job
        is to report broken things."""
        try:
            h = self.health(account_id)
        except Exception as e:
            return _ch.unknown(detail="%s: %s" % (type(e).__name__, e),
                               source=self.kind or self.id)
        return h if isinstance(h, _ch.Health) else _ch.unknown(
            detail="adapter returned %r" % type(h).__name__, source=self.id)

    #: The legacy vocabulary /api/connectors has always spoken. Mapped FROM the
    #: derived verdict rather than computed a second way, so the new field and
    #: the old one can never disagree. `degraded` is new and is the honest word
    #: for "connected, and one thing it offers is switched off".
    _LEGACY = {
        _ch.WORKING: "connected",
        _ch.DEGRADED: "degraded",
        _ch.NEEDS_USER: "error",
        _ch.UNREADABLE: "error",
        _ch.ABSENT: "disconnected",
        _ch.UNKNOWN: "unknown",
    }

    def to_status_dict(self) -> dict:
        h = self._safe_health()
        accounts = []
        try:
            accounts = [a.as_dict() for a in (self.accounts() or [])]
        except Exception:
            accounts = []
        legacy = self._LEGACY.get(h.state, "unknown")
        return {
            "key": self.id,
            "name": self.label or self.id,
            "icon": self.icon,
            "category": self.category,
            "kind": self.kind,
            "blurb": self.blurb,
            "capabilities": list(self.capabilities),
            "workspaces": list(self.workspaces),
            "setup_hint": self.setup_hint,
            "docs_url": self.docs_url,
            "fields": [f.as_dict() for f in self.auth().fields],
            "auth_mode": self.auth().mode,
            "multi_account": bool(self.multi_account),
            "accounts": accounts,
            # The legacy contract, unchanged in shape.
            "status": legacy,
            "detail": h.detail or h.summary,
            "connected": h.healthy,
            "tool_count": 0,
            "tools": [],
            # The derived verdict, alongside. Anything reading `health` gets
            # the vocabulary that distinguishes whose problem this is.
            "health": h.as_dict(),
        }
