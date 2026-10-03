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
"""
from __future__ import annotations

from flask import jsonify, request

FEDERATION = "federation"
#: The trust graph's agent kind: held behind FEDERATION and this second
#: switch, so Federation coming back does not by itself start scoring agents.
TRUST_AGENTS = "trust_agents"
NOT_ENABLED_MESSAGE = "Not enabled in this release."

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


def not_enabled_body(feature: str) -> dict:
    return {"ok": False, "error": "not_enabled", "feature": feature,
            "message": NOT_ENABLED_MESSAGE}


def federation_route_gate():
    """A blueprint before_request hook: answers NOT_ENABLED for a held
    federation route while the switch is off; None lets the route run."""
    path = request.path or ""
    if path in _FEDERATION_OPEN_PATHS or not path.startswith(_FEDERATION_PREFIXES):
        return None
    if enabled(FEDERATION):
        return None
    return jsonify(not_enabled_body(FEDERATION)), 404
