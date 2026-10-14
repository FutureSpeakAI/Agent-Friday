"""cloud_preferred: when the local brain may answer in the cloud's place.

Only in ``cloud_preferred``, and only when the cloud is out: there is no cloud
key, or the cloud call failed with a connection or transport error. A refusal
(a spend cap, a policy hold, a missing intent) or an HTTP error the provider
sent on purpose is NOT an outage and never falls back. ``cloud_only`` never
falls back at all.

The answer opens with the fixed words of ``laya_templates.OUTAGE_LOCAL`` and
carries the Laya receipt (``services/cloud_intent.outage_notice``).
"""
from __future__ import annotations

#: Exception class names (anywhere in the MRO) that mean "could not reach it".
_TRANSPORT_NAMES = frozenset({
    "ConnectionError", "ConnectionRefusedError", "ConnectionResetError",
    "ConnectionAbortedError", "TimeoutError", "APIConnectionError",
    "APITimeoutError", "ConnectError", "ConnectTimeout", "ReadTimeout",
    "WriteTimeout", "PoolTimeout", "TimeoutException", "TransportError",
    "NetworkError", "RemoteProtocolError", "ReadError", "gaierror",
    "NewConnectionError", "MaxRetryError", "SSLError", "ProxyError",
})
#: Never an outage, whatever else the exception looks like.
_REFUSAL_NAMES = frozenset({
    "NoCloudIntent", "LocalUnavailable", "SpendCapReached", "CapExceeded",
    "TaskBudgetExceeded", "UserFacingError", "AuthenticationError",
    "PermissionDeniedError", "BadRequestError", "RateLimitError",
})


def _names(exc) -> set:
    return {c.__name__ for c in type(exc).__mro__}


def is_transport_error(exc) -> bool:
    """True for a failure to reach the cloud (connection, DNS, timeout)."""
    if exc is None:
        return False
    names = _names(exc)
    if names & _REFUSAL_NAMES:
        return False
    if getattr(exc, "status_code", None) is not None:
        return False                      # the provider answered; that is not an outage
    return bool(names & _TRANSPORT_NAMES)


def may_fall_back(mode, *, no_key: bool = False, exc=None) -> bool:
    """May the local brain answer instead, under ``mode``?"""
    from agent_friday.services import cloud_intent
    if not cloud_intent.outage_local_allowed(mode):
        return False
    return bool(no_key) or is_transport_error(exc)


def provider_label(provider, route_info=None) -> str:
    """The name the notice gives the unreachable provider."""
    info = route_info or {}
    if str(provider or "") == "openai":
        return str(info.get("provider_name") or "OpenAI")
    return "Anthropic"


def local_model_for(mode, exc, settings, om_models=None):
    """The local model that may answer this outage, or None.

    None unless ``may_fall_back`` allows it and an installed on-device model
    exists. ``om_models`` is injectable for tests; otherwise Ollama is asked.
    """
    if not may_fall_back(mode, exc=exc):
        return None
    return pick_local_model(settings, om_models)


def pick_local_model(settings, om_models=None):
    cfg = (settings or {}).get("model_routing") or {}
    if om_models is None:
        try:
            from agent_friday.routing.ollama_manager import get_manager
            from agent_friday.services.local_only_guard import is_cloud_model_tag
            om = get_manager(cfg.get("ollama_url", "http://localhost:11434"))
            om_models = ([m for m in (om.list_models() or [])
                          if not is_cloud_model_tag(m.get("name"))]
                         if om.is_available() else [])
        except Exception:
            om_models = []
    if not om_models:
        return None
    wanted = cfg.get("local_model")
    if wanted and any(m.get("name") == wanted for m in om_models):
        return wanted
    return min(om_models, key=lambda m: m.get("size") or m.get("size_gb") or 1e18)["name"]


def announce(reply, provider: str):
    """(reply opened with the fixed notice, the Laya receipt)."""
    from agent_friday.services import cloud_intent
    note = cloud_intent.outage_notice(provider)
    return "%s\n\n%s" % (note.text, reply or ""), note.receipt()
