"""Which installed model serves a role — asked once, in one place.

A hardcoded model name is a dangling pointer the moment someone runs
`ollama rm`, and on 2026-08-18 Stephen ran it three times: `gemma4:e2b`,
`gemma4:12b` and `gemma4:26b` left the daemon while three separate modules
still named them as constants. Every one of those modules failed differently
and none of them said why:

  * `judgment_gate.DEFAULT_JUDGE_MODEL` 404'd on every probe in the boot
    battery, which crawled startup and left the privacy gate quietly degraded
    to its deterministic verdict;
  * `research/harness.BRAIN` 404'd mid-commission and surfaced as "no usable
    JSON", which the pipeline is entitled to read as "the model would not
    comply" — so a missing model was reported as a research finding;
  * `EXTRACTOR` and `HEAVY` were the same bug waiting for the next stage.

The failure mode they share is worse than the outage: each one degrades into
something that looks like a legitimate negative result. A model that is not
installed is not a model that had nothing to say.

So the question "what should serve role X" is answered here, against what the
daemon actually reports, and a substitution is always announced. There is no
list of names to keep in sync — the roles map onto `capability_routing`, which
is the setting Stephen already edits, and the size-ordered fallback is only
reached when that points at something absent too.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.request

_log = logging.getLogger("friday.local_seats")

# role -> the capability_routing key that names its preferred seat
_ROLE_TO_CAPABILITY = {
    "brain": "reasoning",
    "judge": "reasoning",
    "sidekick": "subagent",
    "extractor": "subagent",
    "heavy": "heavy_hitter",
}

# Roles that want the biggest thing available rather than the cheapest.
_WANTS_LARGE = {"heavy"}

# Below this, a model is a toy for this purpose: `functiongemma:270m` is a
# 270M function-caller and will not extract passages or return a verdict.
_MIN_USEFUL_GB = 1.5

# Vision-language tags. These answer text, but they are not the seat anyone
# means by "reasoning", so they lose ties in the size-ordered fallback.
_VISION_HINTS = ("-vl", "vision", "llava", "-vl:", "minicpm-v")


def _looks_vision(name: str) -> bool:
    n = (name or "").lower()
    return any(h in n for h in _VISION_HINTS)


_CACHE: dict = {"at": 0.0, "rows": []}
_CACHE_TTL_S = 30.0

# So a substitution is logged once, not once per call in a grinding loop.
_ANNOUNCED: set = set()


def _friday_store() -> list[tuple[str, float]]:
    """(model_id, size_gb) from Friday's OWN registry.

    ~/.friday/runtime/models/models.json is the manifest the Arbiter serves
    from -- her runtime, not the daemon's. A file that is listed but missing
    from disk does not count.
    """
    out: list[tuple[str, float]] = []
    try:
        import os
        import pathlib
        home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
        raw = pathlib.Path(home, ".friday", "runtime", "models",
                           "models.json").read_text("utf-8")
        for mid, rec in ((json.loads(raw) or {}).get("models") or {}).items():
            if not isinstance(rec, dict) or rec.get("is_embedding"):
                continue
            path = rec.get("path")
            if path and not pathlib.Path(path).exists():
                continue
            out.append((mid, float(rec.get("size_bytes") or 0) / 1e9))
    except Exception as e:
        _log.debug("could not read Friday's model store: %s", e)
    return out


def installed(force: bool = False) -> list[tuple[str, float]]:
    """(name, size_gb) for every seat the daemon can serve, smallest first.

    Embedding models are excluded: they cannot answer, and offering one as a
    fallback would turn a missing-model problem into a baffling one.
    Returns [] when the daemon cannot be reached — callers must read that as
    "unknown", never as "nothing is installed".
    """
    now = time.time()
    if not force and _CACHE["rows"] and (now - _CACHE["at"]) < _CACHE_TTL_S:
        return list(_CACHE["rows"])

    # FRIDAY'S OWN STORE COUNTS AS INSTALLED.
    #
    # This module originally asked only the Ollama daemon, and that was wrong
    # in a way that inverted its purpose. `gemma4:e2b`, `gemma4:12b`, `e4b`
    # and `26b` are real entries in ~/.friday/runtime/models/models.json with
    # files on disk and extracted templates -- Friday's own runtime, which the
    # Arbiter serves as processes it owns. Asking the daemon about them
    # returns nothing, so a resolver that trusts the daemon alone concludes
    # her own models are missing and substitutes whatever happens to have been
    # `ollama pull`ed. That is not healing a dangling pointer; it is moving a
    # seat off the runtime Stephen chose, and on 2026-08-18 it did exactly
    # that to his reasoning seat.
    #
    # "Installed" means "Friday can serve it", from either store.
    rows = list(_friday_store())
    seen = {n for n, _ in rows}
    try:
        from agent_friday.services.local_call import ollama_url
        base = ollama_url().rstrip("/")
    except Exception:
        base = "http://localhost:11434"
    daemon_names: set = set()
    try:
        with urllib.request.urlopen(f"{base}/api/tags", timeout=4) as r:
            raw = json.loads(r.read().decode()).get("models", [])
        for m in raw:
            n = m.get("name")
            if not n:
                continue
            # Recorded even when the store already listed it: the daemon having
            # the name is what makes it callable without a live llama.cpp seat.
            daemon_names.add(n)
            if n not in seen:
                seen.add(n)
                rows.append((n, float(m.get("size") or 0) / 1e9))
    except Exception as e:
        _log.debug("could not read the Ollama inventory: %s", e)

    rows = [t for t in rows if "embed" not in t[0].lower()]

    # A GGUF ON DISK IS NOT A SEAT A CALLER CAN REACH.
    #
    # The union above is right in principle -- Friday's own runtime models are
    # real and the Arbiter can serve them -- but "can be served" and "is being
    # served" are different claims, and this function's callers act on the
    # second one. `local_call` dispatches by NAME: it asks `seat_endpoint()`
    # for a live llama.cpp seat and, finding none, falls through to the Ollama
    # daemon. A model that exists only in Friday's store therefore resolves to
    # a name the daemon has never heard of.
    #
    # Measured on this machine 2026-08-24. `resolve()` returned 'gemma4:e2b'
    # for judge, orchestrator, sidekick, interactive_brain, function_manager
    # and memory_manager. `seat_endpoint('gemma4:e2b')` was None and Ollama's
    # /api/tags did not list it, so every one of those calls produced
    #
    #     local_call HTTP 404 from gemma4:e2b: model 'gemma4:e2b' not found
    #
    # dozens of times in a burst -- and each 404 escalated to the cloud. A
    # trivial question ("what is two plus two") answered on claude-opus-5 in
    # 46-59s, while the seat that WAS up, gemma4:12b on port 8090, answered the
    # same thing in 1.45s. That is Stephen's long-standing "it took forever
    # then kicked back to the cloud, which I do not want", and the cause was
    # never the router: it was this list promising a model nothing could call.
    #
    # So a Friday-store model counts as installed only while it has a live
    # endpoint. Daemon models are unaffected -- being in /api/tags already means
    # the daemon will serve them on request. This deliberately does NOT try to
    # spawn a seat: that would evict whatever is resident, which is not a
    # decision a name-resolution helper gets to make.
    store_only = {n for n, _ in _friday_store()}
    try:
        from agent_friday.services.local_call import seat_endpoint
    except Exception:
        seat_endpoint = None
    if seat_endpoint is not None and store_only:
        keep, dropped = [], []
        for name, size in rows:
            if name in store_only and name not in daemon_names:
                try:
                    live = bool(seat_endpoint(name))
                except Exception:
                    live = False
                if not live:
                    dropped.append(name)
                    continue
            keep.append((name, size))
        if dropped and keep:
            # Only when it changes the answer, and only once per set.
            sig = ("unreachable", tuple(sorted(dropped)))
            if sig not in _ANNOUNCED:
                _ANNOUNCED.add(sig)
                _log.info("local_seats: %d model(s) on disk have no live seat "
                          "and no daemon entry, so they are not offered: %s",
                          len(dropped), ", ".join(sorted(dropped)))
        # Never strip the list to nothing: an empty result means "unknown" to
        # every caller, which is a worse lie than the one being fixed.
        if keep:
            rows = keep

    rows.sort(key=lambda t: t[1])
    if rows:
        _CACHE.update(at=now, rows=rows)
    return list(rows)


def _configured(role: str) -> str | None:
    key = _ROLE_TO_CAPABILITY.get(role)
    if not key:
        return None
    cr = {}
    try:
        from agent_friday.core import _load_settings
        cr = (_load_settings() or {}).get("capability_routing") or {}
    except Exception:
        pass
    if not cr:
        # Read the file directly. `_load_settings` is not always available this
        # early — measured at boot 2026-08-18, where the judgment gate asked
        # before settings were warm, got nothing, and fell through to the
        # size-ordered guess. It picked a vision model over the reasoning seat
        # Stephen had actually configured, purely because of import order.
        try:
            import os
            import pathlib
            home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
            raw = pathlib.Path(home, ".friday", "settings.json").read_text("utf-8")
            cr = (json.loads(raw) or {}).get("capability_routing") or {}
        except Exception:
            return None
    return ((cr.get(key) or {}).get("model")) or None


def _announce(role: str, wanted: str | None, got: str) -> None:
    sig = (role, wanted, got)
    if sig in _ANNOUNCED:
        return
    _ANNOUNCED.add(sig)
    if wanted:
        msg = (f"  [seats] {role}: {wanted!r} is not installed — using {got!r} "
               f"instead")
    else:
        msg = f"  [seats] {role}: using {got!r}"
    print(msg)
    _log.info(msg.strip())


def resolve(role: str, configured: str | None = None) -> str | None:
    """The best installed seat for `role`, or None if nothing can serve it.

    `configured` is the caller's own preference — a module constant or an
    explicit setting. It wins whenever it is actually installed, so this never
    overrides a working choice; it only replaces a name that has gone missing.

    When the daemon is unreachable the caller's preference is returned
    unchanged. Refusing to guess is right here: a transient daemon blip must
    not permanently rewrite which model Stephen asked for.
    """
    rows = installed()
    if not rows:
        return configured or _configured(role)

    names = {n for n, _ in rows}
    wanted = configured or _configured(role)

    for candidate in (configured, _configured(role)):
        if candidate and candidate in names:
            if candidate != wanted:
                _announce(role, wanted, candidate)
            return candidate

    useful = [t for t in rows if t[1] >= _MIN_USEFUL_GB] or rows
    # A vision-language model answers text fine, but it is not what anyone
    # means by "the reasoning seat", and picking one purely because it is a
    # few hundred MB smaller than the text model beside it is the kind of
    # silently-worse choice this module exists to prevent. Measured
    # 2026-08-18: healing Stephen's reasoning seat chose qwen3-vl:8b (6.14 GB)
    # over his own Gemma-4-E4B (6.33 GB). Prefer text; fall back to vision
    # only when there is nothing else.
    text_only = [t for t in useful if not _looks_vision(t[0])] or useful

    # A SEAT THAT IS ALREADY UP BEATS A SMALLER ONE THAT IS NOT.
    #
    # The size ordering answers "which is cheapest to run", but that is the
    # wrong question when one candidate is ALREADY RUNNING. Substituting for a
    # missing model is a repair, and a repair should land on the seat that
    # costs nothing to reach rather than the one that happens to be smallest.
    #
    # Measured 2026-08-24, healing the six roles that pointed at the absent
    # gemma4:e2b: size ordering chose SmolLM3-3B (1.9 GB, not loaded, would
    # need pulling into memory) while gemma4:12b was live on port 8090 and
    # answering in 1.45s. The smaller model is also markedly weaker for judging
    # and reasoning, so the cheap-looking choice was worse on both axes.
    #
    # Live seats first, then the existing rule inside each group, so nothing
    # about the vision/text or large/small preferences changes.
    try:
        from agent_friday.services.local_call import seat_endpoint

        def _live(name: str) -> bool:
            try:
                return bool(seat_endpoint(name))
            except Exception:
                return False

        live = [t for t in text_only if _live(t[0])]
    except Exception:
        live = []
    pool = live or text_only
    pick = pool[-1][0] if role in _WANTS_LARGE else pool[0][0]
    _announce(role, wanted, pick)
    return pick


def refresh() -> dict:
    """Re-read the inventory and report the current role assignment."""
    installed(force=True)
    return {role: resolve(role) for role in sorted(_ROLE_TO_CAPABILITY)}


# ── healing settings that name a model which is gone ────────────────────────

# capability_routing key -> the legacy flat key that mirrors it. Kept here as
# well as in core because the heal has to fix BOTH sides: `_sync_capability_
# routing` copies the flat key over the canonical one on every save, so
# repairing only `capability_routing` lasts exactly until the next write.
_FLAT_MIRROR = {
    "reasoning": "orchestrator_model",
    "subagent": "subagent_model",
    "heavy_hitter": None,
}

# Which role's preferences to use when repairing a given capability.
_CAP_ROLE = {"reasoning": "brain", "subagent": "sidekick",
             "heavy_hitter": "heavy"}


def _is_local_name(model: str) -> bool:
    """True when this id dispatches to the local daemon.

    Cloud ids must never be touched: they are not in `ollama list` and
    "not installed" says nothing about whether they work.
    """
    try:
        from agent_friday.routing.model_router import provider_family
        return provider_family(model) == "local"
    except Exception:
        return ":" in (model or "")


def heal(settings: dict) -> list[str]:
    """Replace local model names that are no longer installed. Returns notes.

    Stephen's `orchestrator_model` sat at `gemma4:e2b` after he uninstalled
    it, and because the flat key outranks `capability_routing` on any save
    that does not explicitly set routing, every settings write re-stamped the
    dead name over whatever the model picker had just chosen. That is the
    "I change the model and it does not stick" defect, and it survived three
    fixes aimed at the picker because the picker was never the thing that was
    wrong.

    Deliberately conservative:
      * only LOCAL ids are considered — a cloud id absent from `ollama list`
        means nothing;
      * only when the daemon actually answered — an unreachable daemon must
        not rewrite his choices;
      * mutates in place and returns what it changed, so the caller can say so
        rather than healing in silence.
    """
    notes: list[str] = []
    rows = installed()
    if not rows:
        return notes
    names = {n for n, _ in rows}

    cr = settings.get("capability_routing")
    if not isinstance(cr, dict):
        return notes

    for cap, flat in _FLAT_MIRROR.items():
        entry = cr.get(cap)
        entry_model = (entry or {}).get("model") if isinstance(entry, dict) else None
        flat_model = settings.get(flat) if flat else None

        for value in (entry_model, flat_model):
            if not value or not _is_local_name(value) or value in names:
                continue
            fixed = resolve(_CAP_ROLE.get(cap, "brain"), value)
            if not fixed or fixed == value:
                continue
            if isinstance(entry, dict) and entry.get("model") == value:
                entry["model"] = fixed
            if flat and settings.get(flat) == value:
                settings[flat] = fixed
            notes.append(f"{cap}: {value} is not installed - now {fixed}")
            break

    return notes
