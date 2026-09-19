"""One catalogue of what Friday can connect to.

Phase 4 of docs/design/connector-ecosystem.md. There were nine lists of what
COULD be connected (§2.4), and which one you had to edit depended on what kind
of thing you were adding - a distinction §2.1 says should not exist:

    connectors.CONNECTOR_DEFS          6 entries, the only one with a field schema
    platforms.ADAPTER_MODULES         12 entries, module name -> canonical id
    channels.CHANNELS                  2 entries, and until today a literal
                                       repeated in three functions
    provider_registry.DEFAULT_PROVIDERS + PROVIDER_TEMPLATES
    agent._default_mcp_servers()
    capability_state.PROBES            a fixed 5-tuple
    capability_router.CAPABILITIES
    capability_preflight.CAPABILITIES
    cloud_voice.PROVIDERS

WHAT THIS UNIFIES, AND WHAT IT DOES NOT. The first three are catalogues of
CONNECTORS - things with an account and a credential, that appear on the
connectors page - and they are unified here. The rest are catalogues of
CAPABILITIES ("can Friday do vision") or of MODEL PROVIDERS, which are a
different axis: a capability is not something you log in to, and collapsing
them together would produce one list that answers no question well. They are
named above so the next person can see the boundary was chosen rather than
missed.

AN ENUMERATION POINT, NOT A REPLACEMENT. The existing catalogues keep owning
their own mechanics - CONNECTOR_DEFS still carries the field schema the connect
form renders, ADAPTER_MODULES still drives tolerant imports. What did not exist
anywhere is a single answer to "what can Friday connect to", which is why
`/api/connectors` could omit twelve things for months without anyone being able
to notice by reading a list.

Declaring a NEW connector is `register()`, one call, regardless of kind.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field, replace

_LOCK = threading.RLock()

#: Connectors declared at runtime, by id. Kept apart from the derived entries
#: so a reload of the underlying catalogues cannot silently drop them.
_REGISTERED: dict = {}

CATEGORIES = ("productivity", "publishing", "messaging", "intelligence",
              "development", "other")


@dataclass(frozen=True)
class RegistryEntry:
    """One thing Friday can connect to. Describes it; does not operate it."""

    id: str
    label: str = ""
    icon: str = "🔌"
    category: str = "other"
    #: Which mechanism implements it: oauth | mcp | platform | channel.
    #: Reported so a disagreement can be traced to its owner.
    kind: str = "other"
    blurb: str = ""
    capabilities: tuple = ()
    workspaces: tuple = ()
    auth_mode: str = "none"
    #: The credential fields a connect form renders. Shape, never values.
    fields: tuple = ()
    docs_url: str = ""
    setup_hint: str = ""
    multi_account: bool = False
    #: Where this declaration came from, for tracing.
    source: str = ""

    def __post_init__(self):
        if not self.label:
            object.__setattr__(self, "label",
                               self.id.replace("_", " ").title())
        if self.category not in CATEGORIES:
            object.__setattr__(self, "category", "other")

    def as_dict(self) -> dict:
        return {
            "id": self.id, "label": self.label, "icon": self.icon,
            "category": self.category, "kind": self.kind, "blurb": self.blurb,
            "capabilities": list(self.capabilities),
            "workspaces": list(self.workspaces),
            "auth_mode": self.auth_mode,
            "fields": [dict(f) for f in self.fields],
            "docs_url": self.docs_url, "setup_hint": self.setup_hint,
            "multi_account": self.multi_account, "source": self.source,
        }


def register(entry: RegistryEntry) -> RegistryEntry:
    """Declare a connector. One call, whatever kind it is."""
    if not isinstance(entry, RegistryEntry) or not entry.id:
        raise ValueError("register() needs a RegistryEntry with an id")
    with _LOCK:
        _REGISTERED[entry.id] = entry
    return entry


def _from_connector_defs() -> list:
    """The MCP and OAuth connectors (services/connectors.py)."""
    out = []
    try:
        from agent_friday.services.connectors import (
            CONNECTOR_DEFS, CONNECTOR_ORDER,
        )
    except Exception:
        return out
    order = list(CONNECTOR_ORDER) + [k for k in CONNECTOR_DEFS
                                     if k not in CONNECTOR_ORDER]
    for key in order:
        d = CONNECTOR_DEFS.get(key)
        if not d:
            continue
        fields = tuple(
            {"key": f.get("key"), "label": f.get("label", f.get("key")),
             "secret": bool(f.get("secret")), "required": bool(f.get("required")),
             "placeholder": f.get("placeholder", "")}
            for f in (d.get("fields") or []))
        out.append(RegistryEntry(
            id=key, label=d.get("name") or key, icon=d.get("icon") or "🔌",
            category=_category_of(d.get("category")), kind=d.get("kind") or "mcp",
            blurb=d.get("blurb") or "",
            capabilities=tuple(d.get("capabilities") or ()),
            workspaces=tuple(d.get("workspaces") or ()),
            auth_mode=("oauth2" if d.get("kind") == "oauth"
                       else "token" if fields else "none"),
            fields=fields, docs_url=d.get("docs_url") or "",
            setup_hint=d.get("setup_hint") or "",
            # Google is the only mechanism that supports several accounts.
            multi_account=(key == "google"),
            source="connectors.CONNECTOR_DEFS"))
    return out


def _from_platforms() -> list:
    """The publishing platforms (services/platforms/)."""
    # The CALL is guarded, not just the import. Guarding only the import left
    # `entries()` able to raise when the platforms package was reachable but
    # broken - which is the exact coupling this catalogue exists to remove,
    # since one failing mechanism would blank the list of all eighteen.
    try:
        from agent_friday.services import connector_adapters as _ca
        conns = _ca.platform_connectors()
    except Exception:
        return []
    out = []
    for conn in conns:
        try:
            auth = conn.auth()
            out.append(RegistryEntry(
                id=conn.id, label=conn.label, icon=conn.icon,
                category="publishing", kind="platform", blurb=conn.blurb,
                capabilities=tuple(conn.capabilities),
                auth_mode=auth.mode,
                fields=tuple(f.as_dict() for f in auth.fields),
                source="platforms.ADAPTER_MODULES"))
        except Exception:
            # One unreadable adapter costs its own entry, never the catalogue.
            continue
    return out


def _from_channels() -> list:
    try:
        from agent_friday.services import connector_adapters as _ca
        conns = _ca.channel_connectors()
    except Exception:
        return []
    out = []
    for conn in conns:
        try:
            auth = conn.auth()
            out.append(RegistryEntry(
                id=conn.id, label=conn.label, icon=conn.icon,
                category="messaging", kind="channel", blurb=conn.blurb,
                capabilities=tuple(conn.capabilities),
                auth_mode=auth.mode,
                fields=tuple(f.as_dict() for f in auth.fields),
                source="channels.CHANNELS"))
        except Exception:
            continue
    return out


#: The words the existing catalogues already use, mapped onto the shared set.
#: Read off CONNECTOR_DEFS rather than guessed: it says "Communication", not
#: "messaging", and a mapping written from imagination silently dropped Slack
#: and Discord into "other".
_CATEGORY_ALIASES = {
    "communication": "messaging", "chat": "messaging", "messaging": "messaging",
    "social": "publishing", "publishing": "publishing",
    "productivity": "productivity", "docs": "productivity",
    "development": "development", "code": "development", "dev": "development",
    "intelligence": "intelligence", "search": "intelligence",
    "knowledge": "intelligence",
}


def _category_of(raw) -> str:
    c = str(raw or "").strip().lower()
    if c in CATEGORIES:
        return c
    return _CATEGORY_ALIASES.get(c, "other")


def entries(include_registered: bool = True) -> list:
    """Everything Friday can connect to, in display order.

    Order is CONNECTOR_DEFS first (the surfaces that have always been there,
    in their established order), then platforms, then channels, then anything
    registered at runtime. Deduped by id, first declaration winning, so a
    mechanism cannot be shadowed by a later catalogue listing the same name.
    """
    out, seen = [], set()
    groups = [_from_connector_defs(), _from_platforms(), _from_channels()]
    if include_registered:
        with _LOCK:
            groups.append(list(_REGISTERED.values()))
    for group in groups:
        for e in group:
            if e.id in seen:
                continue
            seen.add(e.id)
            out.append(e)
    return out


def get(connector_id: str):
    for e in entries():
        if e.id == connector_id:
            return e
    return None


def by_category() -> dict:
    out = {}
    for e in entries():
        out.setdefault(e.category, []).append(e)
    return out


def catalogue() -> dict:
    """What COULD be connected, next to what IS.

    The two questions were never askable together. `/api/connectors` answered
    the second for six of eighteen things, and nothing answered the first at
    all - which is how twelve connectors stayed invisible for months without
    anyone being able to notice by reading a list.
    """
    live = {}
    try:
        from agent_friday.services import connectors as _c
        live = {r["key"]: r for r in _c.list_connectors()}
    except Exception:
        live = {}
    rows = []
    for e in entries():
        row = e.as_dict()
        cur = live.get(e.id)
        row["status"] = (cur or {}).get("status", "unknown")
        row["connected"] = bool((cur or {}).get("connected"))
        row["health"] = (cur or {}).get("health")
        rows.append(row)
    return {
        "connectors": rows,
        "total": len(rows),
        "connected": sum(1 for r in rows if r["connected"]),
        "categories": sorted({r["category"] for r in rows}),
        "kinds": sorted({r["kind"] for r in rows}),
    }


def _reset_for_tests() -> None:
    with _LOCK:
        _REGISTERED.clear()
