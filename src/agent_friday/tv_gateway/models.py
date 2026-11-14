"""The PC's model slate, as one gateway contract for the phone, the tablet and the TV.

Stephen, 2026-11-14: he picked Opus on the tablet, sent a voice chat and got nothing; the tablet's
selector had no slate at all (``GET /tv/v1/models`` was a 404, the route never existed) and showed none
of the PC's local models or OpenRouter. The promises:

* ``GET /tv/v1/models`` returns the PC's WHOLE chat slate from the same source the desktop selector reads
  (services/model_catalog through the warm cache, never rebuilt per request): local seats, Anthropic,
  OpenAI, Google, OpenRouter's discovered models and any other configured provider. Per model: a stable id,
  a display name, local or cloud, provider, whether it can be used now (and if not, why, in words),
  warm or cold, a rough speed and whether it suits a spoken turn. Rough speed and voice suitability come
  from the model's name and size and are labelled so (``speed_basis``): they are a guide, not a benchmark.
* the list is scoped by who is asking. An owner-bound personal device (phone, tablet, watch) may select
  any model the PC can use now. A TV, a family device or an unbound one sees the slate (it is the
  household's own) but selects nothing: it is answered by the household's routing, and says so.
* a pick is honoured or refused loudly. ``resolve`` turns what a device sent ("claude-opus-5-5", a
  display name, or a family word such as "opus") into one listed model, or raises a refusal that names the
  reason ("Claude Opus 5.5 needs an Anthropic key on your PC"; "OpenRouter is offline"; "no model called
  'opuss'", with the nearest ids). It never returns silence and never picks another model.

Nothing here edits the catalog; it only reads it.
"""
from __future__ import annotations

import re
from typing import List, Optional

FAMILIES = ("opus", "sonnet", "haiku", "fable")
_FAST = re.compile(r"haiku|mini|nano|flash|lite|small|instant|turbo|e2b|e4b|1\.7b|\b[1-9]b\b|3\.5-9b", re.I)
_SLOW = re.compile(r"opus|\bo1\b|\bo3\b|reason|think|r1\b|pro\b|27b|32b|35b|70b|72b|120b|405b|fable", re.I)


class ModelRefused(Exception):
    """A pick that cannot run. ``status`` is the HTTP code, ``code`` a stable word for clients."""

    def __init__(self, status: int, code: str, reason: str, **extra):
        super().__init__(reason)
        self.status, self.code, self.reason, self.extra = status, code, reason, extra


def raw_catalog() -> dict:
    """The catalog the desktop selector reads, from the warm cache (so it is never a 10-20 s rebuild)."""
    from agent_friday.services import warm_cache
    from agent_friday.services.model_catalog import build_catalog
    warm_cache.register("model_catalog", build_catalog, ttl_s=900.0)
    got = warm_cache.get("model_catalog", compute_if_cold=True) or {}
    return got.get("value") or {"roles": {}, "models": [], "providers": []}


def _selected(settings: Optional[dict] = None) -> str:
    try:
        if settings is None:
            from agent_friday.core import _load_settings
            settings = _load_settings() or {}
        return str(settings.get("orchestrator_model") or "")
    except Exception:
        return ""


def _provider_state(cat: dict) -> dict:
    return {p.get("name"): p for p in cat.get("providers") or [] if isinstance(p, dict)}


def _is_chat(m: dict) -> bool:
    roles = m.get("roles") or []
    if "orchestrator" in roles or "subagent" in roles:
        return True
    mods = m.get("modalities") or []
    return "text" in mods and not ({"image", "video", "audio", "3d"} & set(mods) - {"vision"})


def speed_of(m: dict) -> str:
    """Rough speed class from the name: ``fast``, ``medium`` or ``slow``. A guide, not a measurement."""
    s = "%s %s" % (m.get("id") or "", m.get("label") or "")
    if _FAST.search(s):
        return "fast"
    if _SLOW.search(s):
        return "slow"
    return "medium"


def why_unavailable(m: dict, providers: dict) -> str:
    """In words, why this model cannot be used now."""
    label = m.get("label") or m.get("id")
    prov = providers.get(m.get("provider")) or {}
    plabel = m.get("provider_label") or prov.get("label") or m.get("provider") or "its provider"
    if m.get("needs_key") and (m.get("available") is False or prov.get("available") is False):
        art = "an" if str(plabel)[:1].upper() in "AEIOU" else "a"
        return "%s needs %s %s key on your PC (add it in Settings > Keys)" % (label, art, plabel)
    if m.get("local"):
        return "%s is not running on your PC" % label
    return "%s is offline or unreachable from your PC right now" % plabel


def rows(dev: Optional[dict] = None, cat: Optional[dict] = None, settings: Optional[dict] = None) -> List[dict]:
    """The slate for ``dev``: curated chat models first, then the rest, each as the contract describes."""
    cat = cat if cat is not None else raw_catalog()
    from agent_friday.tv_gateway import owner_turn
    owner = owner_turn.is_owner_device(dev) if dev else False
    providers = _provider_state(cat)
    selected = _selected(settings)
    curated_ids = {m.get("id") for m in (cat.get("roles") or {}).get("orchestrator") or []}
    order = {m.get("id"): i for i, m in enumerate((cat.get("roles") or {}).get("orchestrator") or [])}
    seen, out = set(), []
    pool = list((cat.get("roles") or {}).get("orchestrator") or []) + [m for m in cat.get("models") or []
                                                                      if isinstance(m, dict) and _is_chat(m)]
    for m in pool:
        mid = m.get("id")
        if not mid or mid in seen or not isinstance(m, dict):
            continue
        seen.add(mid)
        ok = m.get("available") is not False
        local = bool(m.get("local"))
        resident = m.get("resident")
        warm = bool(resident) if resident is not None else (True if (local and mid == selected and ok) else
                                                            (None if not local else False))
        speed = speed_of(m)
        selectable = bool(owner and ok)
        row = {"id": mid, "name": m.get("label") or mid, "provider": m.get("provider"),
               "provider_name": m.get("provider_label") or m.get("provider"),
               "kind": "local" if local else "cloud", "available": ok,
               "reason": None if ok else why_unavailable(m, providers),
               "key_present": None if not m.get("needs_key") else ok,
               "warm": warm, "speed": speed, "speed_basis": "model name; a guide, not a measurement",
               "voice": {"suitable": speed != "slow", "why": "fast enough to speak" if speed != "slow"
                         else "thinks for a long time; better for typed questions"},
               "tools": bool(m.get("supports_tools")) if m.get("supports_tools") is not None else None,
               "curated": mid in curated_ids, "selected": mid == selected,
               "selectable": selectable}
        if ok and not owner:
            row["reason"] = ("this device is answered by the household's own model; "
                             "models are chosen on the owner's phone or tablet")
        out.append(row)
    # The selected model first, then the curated seats in the catalog's own order (newest first, which is what
    # a family word like "opus" relies on), then the rest: local before cloud, by name.
    out.sort(key=lambda r: (not r["selected"], not r["curated"],
                            order.get(r["id"], 0) if r["curated"] else 0,
                            r["kind"] != "local" if not r["curated"] else False,
                            "" if r["curated"] else r["name"].lower()))
    return out


def resolve(requested: str, slate: List[dict]) -> dict:
    """The one listed model a device's pick means, or ModelRefused naming why not.

    ``slate`` is ``rows()`` for an owner device. Exact id first, then a display name (case-blind), then
    a family word ("opus": the first listed Opus, curated and newest first). Never another model."""
    req = str(requested or "").strip()
    low = req.lower()
    hit = next((r for r in slate if r["id"] == req), None)
    if hit is None:
        hit = next((r for r in slate if r["name"].lower() == low or r["id"].lower() == low), None)
    if hit is None and low in FAMILIES:
        family = [r for r in slate if low in (r["id"] + " " + r["name"]).lower()]
        usable = [r for r in family if r["available"]]
        if usable:
            hit = usable[0]
        elif family:
            raise ModelRefused(409, "model_unavailable", family[0]["reason"] or "that model is not available",
                               model=family[0]["id"], requested=req)
    if hit is None:
        near = [r["id"] for r in slate if low and (low in r["id"].lower() or low in r["name"].lower())][:6]
        raise ModelRefused(400, "unknown_model", "this computer has no model called %r" % req,
                           requested=req, did_you_mean=near)
    if not hit["available"]:
        raise ModelRefused(409, "model_unavailable", hit["reason"] or "that model is not available",
                           model=hit["id"], requested=req)
    return hit
