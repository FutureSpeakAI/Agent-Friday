"""Features held back from this release.

A held feature keeps its code, routes and data. While its switch in
`settings.held_features` is off, nothing offers it: no workspace, dock entry,
Settings tab, palette item, navigate target or content platform, and its
routes answer a plain "not enabled in this release" result.

`federation` holds the Marketplace, positrons (the economy), peer federation,
federated compute and defederation. The identity and attestation routes are
not part of it and stay mounted whatever the switch says.

Buying is outside every switch here: services/marketplace.py refuses a
purchase in every configuration of this release.

Inside `federation` the Federation / Economy work has six logical capabilities
(spec section 36), each its own switch `fed_<name>` in the same dict, off when
absent, plus one kill switch `fed_kill_switch`. A capability is on only when
`federation` is on, its own switch is exactly True and the kill switch is not.
`public_discovery` and `live_cash` are never on in this release: setting them
changes nothing (like a purchase, which no switch opens).
"""
from __future__ import annotations

from flask import jsonify, request

FEDERATION = "federation"
#: The trust graph's agent kind: held behind FEDERATION and this second
#: switch, so Federation coming back does not by itself start scoring agents.
TRUST_AGENTS = "trust_agents"
NOT_ENABLED_MESSAGE = "Not enabled in this release."

#: The six logical capabilities of the Federation / Economy spec, each off until its own
#: switch `fed_<name>` is True (and the federation switch is on, and the kill switch is not).
OFFLINE_REHEARSAL = "offline_rehearsal"      # test credits on this PC
INVITED_JOBS = "invited_jobs"                # manual jobs with an invited peer
UNATTENDED_WORKER = "unattended_worker"      # a peer's job runs here on an approved card
PRIVATE_PUBLISH = "private_publish"          # listings seen only by invited peers
PUBLIC_DISCOVERY = "public_discovery"        # never on in this release
LIVE_CASH = "live_cash"                      # never on in this release
CAPABILITIES = (OFFLINE_REHEARSAL, INVITED_JOBS, UNATTENDED_WORKER, PRIVATE_PUBLISH,
                PUBLIC_DISCOVERY, LIVE_CASH)
NEVER_IN_THIS_RELEASE = frozenset({PUBLIC_DISCOVERY, LIVE_CASH})
KILL_SWITCH = "fed_kill_switch"

# Route prefixes the federation switch holds, and the exact paths inside them
# that stay open: the agent's own identity card and the well-known document
# peers read it from.
_FEDERATION_PREFIXES = (
    "/api/federation/",
    "/api/marketplace/",
    "/api/economy/",
    "/api/compute/",
    "/api/defederation/",
)
_FEDERATION_OPEN_PATHS = frozenset({
    "/api/federation/identity",
    "/.well-known/friday-agent.json",
    # Refused in every configuration by its own route, never "not enabled".
    "/api/marketplace/purchase",
})


def enabled(feature: str, settings: dict | None = None) -> bool:
    """True only when settings.held_features[feature] is exactly True. A
    missing, malformed or unreadable setting reads as off."""
    if settings is None:
        try:
            from agent_friday.core import _load_settings
            settings = _load_settings()
        except Exception:
            return False
    held = (settings or {}).get("held_features")
    return isinstance(held, dict) and held.get(feature) is True


def kill_switch_engaged(settings: dict | None = None) -> bool:
    """True when the owner has stopped all Federation / Economy activity (exactly True)."""
    return enabled(KILL_SWITCH, settings)


def capability_enabled(capability: str, settings: dict | None = None) -> bool:
    """True only for a known, releasable capability whose own switch is exactly True while
    the federation switch is on and the kill switch is not. Anything else, including an
    unreadable setting, is off."""
    if capability not in CAPABILITIES or capability in NEVER_IN_THIS_RELEASE:
        return False
    if settings is None:
        try:
            from agent_friday.core import _load_settings
            settings = _load_settings()
        except Exception:
            return False
    return (enabled(FEDERATION, settings) and not kill_switch_engaged(settings)
            and enabled("fed_" + capability, settings))


def not_enabled_body(feature: str) -> dict:
    return {"ok": False, "error": "not_enabled", "feature": feature,
            "message": NOT_ENABLED_MESSAGE}


# The capability a held route belongs to. Longest prefix first; a path under no entry is
# held by the federation switch and the kill switch alone.
_CAPABILITY_BY_PREFIX = (
    ("/api/federation/compute/", UNATTENDED_WORKER),
    ("/api/compute/", UNATTENDED_WORKER),
    ("/api/economy/", OFFLINE_REHEARSAL),
    ("/api/marketplace/", PRIVATE_PUBLISH),
)


def _capability_for(path: str) -> str | None:
    for prefix, capability in _CAPABILITY_BY_PREFIX:
        if path.startswith(prefix):
            return capability
    return None


def federation_route_gate():
    """A blueprint before_request hook: answers NOT_ENABLED for a held federation route
    while the federation switch is off, while the kill switch is engaged, or (for routes
    that belong to one) while that capability's own switch is off; None lets it run."""
    path = request.path or ""
    if path in _FEDERATION_OPEN_PATHS or not path.startswith(_FEDERATION_PREFIXES):
        return None
    try:
        from agent_friday.core import _load_settings
        settings = _load_settings()
    except Exception:
        settings = {}
    if not enabled(FEDERATION, settings):
        return jsonify(not_enabled_body(FEDERATION)), 404
    if kill_switch_engaged(settings):
        return jsonify(not_enabled_body(KILL_SWITCH)), 404
    capability = _capability_for(path)
    if capability and not capability_enabled(capability, settings):
        return jsonify(not_enabled_body("fed_" + capability)), 404
    return None
