"""Model and capability registry validation (north star §34.2-16).

Friday's model and provider facts live in several registries that drift apart
quietly: the built-in providers (``provider_registry.DEFAULT_PROVIDERS``), the
extra hosted providers (``provider_descriptors.BUILTIN_EXTRA_PROVIDERS``), the
per-capability seat map in ``DEFAULT_SETTINGS["capability_routing"]``, the
local provider names ``seat_policy`` trusts, the local brain ladder
(``model_plan``) and the curated download shortlist
(``resources/model_shortlist.json``). A seat that names a provider nobody
registered, or a default model no registry lists, only shows up as a
"can't reach that model" at run time.

``problems()`` returns every inconsistency as a plain sentence; CI fails on
any (tests/unit/test_model_registry_is_valid.py, and
``python scripts/check_model_registry.py`` by hand). Nothing here touches the
network or the person's own provider files: only what ships.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

#: Provider names a seat may use that are not registry entries, and what each
#: one is. A new alias needs a line here, so it is a decision, not drift.
SEAT_ALIASES = {
    # The in-process sentence-transformers embedder (services/minilm.py).
    "local": "the in-process embedder on this computer (services/minilm.py)",
}

#: Local provider spellings seat_policy and model_ids accept that are not
#: registry entries: adapter names and the runtime's own internal seats.
LOCAL_NAME_ALIASES = {
    "local": "the in-process embedder (services/minilm.py)",
    "local-voice": "the local-voice adapter name (provider: local-voice-lite)",
    "nemo-local": "the nemo-local adapter name (provider: nvidia-nemo)",
    "arbiter-local": "the residency arbiter's own seat (services/residency_arbiter.py)",
    "llama-cpp-local": "the llama.cpp server seat (services/seat_binding.py)",
}

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")

SHORTLIST = Path(__file__).resolve().parents[1] / "resources" / "model_shortlist.json"


def _builtin_providers() -> list:
    from agent_friday.services.provider_registry import DEFAULT_PROVIDERS
    from agent_friday.routing.provider_descriptors import BUILTIN_EXTRA_PROVIDERS
    return [dict(p) for p in DEFAULT_PROVIDERS] + [dict(p) for p in BUILTIN_EXTRA_PROVIDERS]


def provider_problems(providers: list | None = None) -> list:
    """Every built-in provider is a valid descriptor; names are unique."""
    from agent_friday.routing import provider_descriptors as pd
    out, seen = [], set()
    for raw in (providers if providers is not None else _builtin_providers()):
        name = str(raw.get("name") or "")
        if not _NAME_RE.match(name):
            out.append("provider %r: the name is not a short lower-case id" % name)
        if name in seen:
            out.append("provider %r is registered twice" % name)
        seen.add(name)
        try:
            ok, errors, _warnings = pd.validate_descriptor(pd.normalize_descriptor(dict(raw)))
        except Exception as e:  # noqa: BLE001
            ok, errors = False, ["%s: %s" % (type(e).__name__, e)]
        if not ok:
            out.append("provider %r is not a valid descriptor: %s" % (name, "; ".join(map(str, errors))))
    return out


def _registered() -> dict:
    return {p["name"]: p for p in _builtin_providers()}


def _ladder_ids() -> set:
    from agent_friday.services import model_plan
    return {str(m["id"]) for m in model_plan.BRAIN_MODELS}


def _shortlist_ids() -> set:
    try:
        data = json.loads(SHORTLIST.read_text(encoding="utf-8"))
        return {str(m.get("id")) for m in data.get("models") or []}
    except Exception:
        return set()


def _known_models(provider: str, registered: dict) -> set:
    from agent_friday.routing import provider_descriptors as pd
    p = registered.get(provider) or {}
    ids = {str(m) for m in (p.get("models") or [])}
    if p and pd.classification_of(pd.normalize_descriptor(dict(p))) == "local":
        # A local runtime serves whatever the ladder or shortlist installs.
        ids |= _ladder_ids() | _shortlist_ids()
    return ids


def seat_problems(routing: dict | None = None) -> list:
    """Every default seat names a registered provider (or a listed alias), every
    capability has a seat, and every default model is one a registry lists."""
    from agent_friday.core import DEFAULT_SETTINGS
    from agent_friday.services.capability_router import CAPABILITIES
    from agent_friday.services import minilm
    routing = routing if routing is not None else DEFAULT_SETTINGS.get("capability_routing") or {}
    registered = _registered()
    out = []
    for cap in CAPABILITIES:
        if cap not in routing:
            out.append("capability %r has no default seat in capability_routing" % cap)
    for seat, cfg in routing.items():
        prov = str((cfg or {}).get("provider") or "")
        model = str((cfg or {}).get("model") or "")
        if prov in SEAT_ALIASES:
            if prov == "local" and model and model != minilm.MODEL:
                out.append("seat %r: the local embedder is %s, not %r" % (seat, minilm.MODEL, model))
            continue
        if prov not in registered:
            out.append("seat %r names provider %r, which is not registered" % (seat, prov))
            continue
        if model and model not in _known_models(prov, registered):
            out.append("seat %r: model %r is not listed by provider %r, the brain ladder or "
                       "the shortlist" % (seat, model, prov))
    return out


def local_name_problems() -> list:
    """seat_policy's local names are registered local providers or listed
    aliases; every registered provider is judged local or cloud the same way
    by seat_policy as by its own descriptor."""
    from agent_friday.services import seat_policy
    from agent_friday.routing import provider_descriptors as pd
    registered = _registered()
    out = []
    for name in sorted(seat_policy.LOCAL_PROVIDER_NAMES):
        if name in registered:
            cls = pd.classification_of(pd.normalize_descriptor(dict(registered[name])))
            if cls != "local":
                out.append("seat_policy treats %r as local, but its descriptor is %s" % (name, cls))
        elif name not in LOCAL_NAME_ALIASES:
            out.append("seat_policy treats %r as local, but no provider or listed alias has that name"
                       % name)
    for name, p in registered.items():
        cls = pd.classification_of(pd.normalize_descriptor(dict(p)))
        if seat_policy.is_local_provider_name(name) != (cls == "local"):
            out.append("provider %r is %s by its descriptor but seat_policy says %s"
                       % (name, cls, "local" if seat_policy.is_local_provider_name(name) else "cloud"))
    return out


def ladder_problems() -> list:
    """The local brain ladder: unique ids, the floor and the default brain are
    ones Friday knows how to get."""
    from agent_friday.services import model_plan, local_seats
    ids = [str(m["id"]) for m in model_plan.BRAIN_MODELS]
    out = ["the brain ladder lists %r twice" % i for i in sorted({i for i in ids if ids.count(i) > 1})]
    if model_plan.FLOOR_MODEL not in ids:
        out.append("the floor model %r is not on the brain ladder" % model_plan.FLOOR_MODEL)
    if local_seats.LOCAL_BRAIN_DEFAULT not in set(ids) | _shortlist_ids():
        out.append("the default local brain %r is on neither the ladder nor the shortlist"
                   % local_seats.LOCAL_BRAIN_DEFAULT)
    from agent_friday.core import DEFAULT_SETTINGS
    jg = str((DEFAULT_SETTINGS.get("judgment_gate") or {}).get("model") or "")
    if jg and jg not in set(ids) | _shortlist_ids():
        out.append("the judgment gate's default model %r is on neither the ladder nor the shortlist" % jg)
    return out


def shortlist_problems(path: Path | None = None) -> list:
    """The download shortlist: unique ids, a known runtime, every file with a
    real sha256 and byte count, exactly one default file per model."""
    path = path or SHORTLIST
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return ["the shortlist cannot be read: %s" % type(e).__name__]
    runtimes = set((data.get("runtime") or {}).keys())
    out, seen = [], set()
    for m in data.get("models") or []:
        mid = str(m.get("id") or "")
        if not mid:
            out.append("a shortlist model has no id")
            continue
        if mid in seen:
            out.append("shortlist model %r is listed twice" % mid)
        seen.add(mid)
        if m.get("runtime") and m["runtime"] not in runtimes:
            out.append("shortlist model %r uses runtime %r, which the shortlist does not define"
                       % (mid, m["runtime"]))
        files = m.get("files") or []
        if not files:
            out.append("shortlist model %r has no files" % mid)
        for f in files:
            fname = f.get("file") or "?"
            if not _SHA_RE.match(str(f.get("sha256") or "")):
                out.append("shortlist %r file %r has no valid sha256" % (mid, fname))
            if not isinstance(f.get("bytes"), int) or f["bytes"] <= 0:
                out.append("shortlist %r file %r has no byte count" % (mid, fname))
        if files and sum(1 for f in files if f.get("default")) != 1:
            out.append("shortlist model %r must mark exactly one file as the default" % mid)
    return out


def problems() -> list:
    """Every registry inconsistency, as sentences. Empty means valid."""
    return (provider_problems() + seat_problems() + local_name_problems()
            + ladder_problems() + shortlist_problems())
