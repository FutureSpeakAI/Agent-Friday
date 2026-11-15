"""The PC's model slate: ONE list, for the desktop selector and for every remote device.

A paired phone or tablet must offer the same models the desktop's picker does (local, Anthropic,
OpenAI, OpenRouter, and so on), say for each whether it can serve right now, and the model a
device picks must either serve the turn or be refused with a reason. This module is the single
source for all three:

* `slate()`            the rows (id, provider, local or cloud, key present, warm or cold,
                       selected, can_serve and the reason when it cannot). Read from the same
                       warm cache `/api/models` serves, so it never costs the 19 s rebuild.
* `why_cannot_serve`   (code, reason) an id cannot serve a turn now, or None.
* `require_servable`   the row, or `ModelNotServable` carrying the reason and a stable `code`.
                       A remote turn calls this BEFORE it starts, so a pick that cannot run is
                       answered at once instead of hanging or silently running another model.

It reads state and changes nothing. A reading that cannot be taken leaves a field None (unknown),
never a guess: "warm" is None when the live seats could not be surveyed.
"""
from __future__ import annotations

from typing import Optional

# Stable codes a client can branch on.
UNKNOWN_MODEL = "unknown_model"
NOT_A_CHAT_MODEL = "not_a_chat_model"
NEEDS_KEY = "needs_key"
PROVIDER_OFF = "provider_unavailable"
LOCAL_ONLY_MODE = "local_only_mode"
LOCAL_BUSY = "local_busy"


class ModelNotServable(Exception):
    """This PC cannot run a turn on the requested model. `code` is stable, `reason` is for people."""

    def __init__(self, model_id: str, code: str, reason: str):
        super().__init__(reason)
        self.model_id, self.code, self.reason = model_id, code, reason


def _catalog() -> dict:
    """The desktop selector's own catalog, from the warm cache (no rebuild per call)."""
    from agent_friday.services import warm_cache
    from agent_friday.services.model_catalog import build_catalog
    warm_cache.register("model_catalog", build_catalog, ttl_s=900.0)
    got = warm_cache.get("model_catalog", compute_if_cold=True)
    return got.get("value") or {"roles": {}, "models": [], "providers": []}


def _settings() -> dict:
    try:
        from agent_friday import core
        return core._load_settings() or {}
    except Exception:
        return {}


def _live_local() -> Optional[set]:
    """Local model ids serving now (warm), or None when the survey could not be read."""
    try:
        from agent_friday.services.machine_probe import snapshot
        from agent_friday.services.residency_arbiter import survey_live_seats

        def survey():
            try:
                return {"live": set((survey_live_seats() or {}).keys()), "failed": False}
            except Exception:
                return {"live": set(), "failed": True}

        result, _at, reading = snapshot("model_slate:live-seats", survey, fresh_for=5.0,
                                        budget=0.1, default=None, allow_blocking=False)
        if result is None or reading != "fresh" or result.get("failed"):
            return None
        return result["live"]
    except Exception:
        return None


def _routing_mode(settings: dict) -> str:
    return str((settings.get("model_routing") or {}).get("mode") or "").strip().lower()


def _is_chat_model(row: dict) -> bool:
    return "orchestrator" in (row.get("roles") or [])


def _blocked(m: dict, mode: str):
    """(code, reason) when this catalog row cannot serve a turn now, else None."""
    mid = m.get("id")
    who = m.get("provider_label") or m.get("provider")
    if not _is_chat_model(m):
        return (NOT_A_CHAT_MODEL, "%s is not a chat model." % mid)
    if not m.get("available"):
        if m.get("needs_key"):
            return (NEEDS_KEY, "%s needs a %s key, and this PC has none. Add it in Settings > "
                    "Connections on the PC." % (mid, who))
        return (PROVIDER_OFF, "%s: %s is not available on this PC right now." % (mid, who))
    if not m.get("local") and mode == "local_only":
        return (LOCAL_ONLY_MODE, "%s is a cloud model and this PC is set to Local only. Change "
                "routing mode on the PC (Settings > Models) to use it." % mid)
    return None


def _row(m: dict, selected: str, live: Optional[set], mode: str) -> dict:
    local = bool(m.get("local"))
    out = {
        "id": m.get("id"), "label": m.get("label") or m.get("id"),
        "provider": m.get("provider"), "provider_label": m.get("provider_label"),
        "local": local, "cloud": not local,
        "needs_key": bool(m.get("needs_key")),
        # A model that needs no key (a local one) has its "key" by definition.
        "key_present": (not m.get("needs_key")) or bool(m.get("available")),
        "available": bool(m.get("available")),
        "warm": (None if live is None else (m.get("id") in live)) if local else None,
        "selected": m.get("id") == selected,
        "curated": bool(m.get("curated")),
        "context_window": m.get("context_window"),
        "hint": m.get("hint"),
    }
    why = _blocked(m, mode)
    out["can_serve"] = why is None
    out["blocked"] = ({"code": why[0], "reason": why[1]} if why else None)
    return out


def slate(*, live: Optional[set] = None) -> dict:
    """Every chat model this PC lists, with where it runs and whether it can serve now."""
    cat = _catalog()
    st = _settings()
    selected = str(st.get("orchestrator_model") or "")
    live = _live_local() if live is None else live
    mode = _routing_mode(st)
    picker = {r.get("id") for r in ((cat.get("roles") or {}).get("orchestrator") or [])
              if isinstance(r, dict)}
    rows, seen = [], set()
    for m in cat.get("models") or []:
        if not isinstance(m, dict) or not _is_chat_model(m) or m.get("id") in seen:
            continue
        seen.add(m.get("id"))
        r = _row(m, selected, live, mode)
        r["in_picker"] = m.get("id") in picker
        rows.append(r)
    # The desktop selector's own membership first; the long tail after.
    rows.sort(key=lambda r: (0 if r["in_picker"] else 1, 0 if r["can_serve"] else 1))
    return {"models": rows, "selected": selected, "routing_mode": mode or None,
            "local_one_at_a_time": True}


def find(model_id: str) -> Optional[dict]:
    """The catalog row for this id (the available copy first), or None."""
    mid = (model_id or "").strip()
    best = None
    for m in _catalog().get("models") or []:
        if isinstance(m, dict) and m.get("id") == mid:
            if m.get("available"):
                return m
            best = best or m
    return best


def _local_busy_reason(mid: str) -> Optional[str]:
    try:
        from agent_friday.routes.conversations import _why_this_seat_cannot_be_bound
        why = _why_this_seat_cannot_be_bound({"model": mid})
        return str(why) if why else None
    except Exception:
        return None  # could not check is not "busy"


def why_cannot_serve(model_id: str) -> Optional[tuple]:
    """(code, reason) this id cannot serve a turn right now, or None when it can."""
    mid = (model_id or "").strip()
    m = find(mid)
    if m is None:
        return (UNKNOWN_MODEL, "This PC does not list a model called %r." % mid)
    blocked = _blocked(m, _routing_mode(_settings()))
    if blocked:
        return blocked
    if m.get("local"):
        busy = _local_busy_reason(mid)
        if busy:
            return (LOCAL_BUSY, busy)
    return None


def require_servable(model_id: str) -> dict:
    """The catalog row, or `ModelNotServable`. Never a quiet other model."""
    why = why_cannot_serve(model_id)
    if why:
        raise ModelNotServable((model_id or "").strip(), why[0], why[1])
    return find(model_id)
