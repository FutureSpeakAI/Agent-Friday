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
    try:
        from agent_friday.services.local_call import ollama_url
        base = ollama_url().rstrip("/")
    except Exception:
        base = "http://localhost:11434"
    try:
        with urllib.request.urlopen(f"{base}/api/tags", timeout=4) as r:
            raw = json.loads(r.read().decode()).get("models", [])
        rows = [(m["name"], float(m.get("size") or 0) / 1e9)
                for m in raw if m.get("name")]
        rows = [t for t in rows if "embed" not in t[0].lower()]
        rows.sort(key=lambda t: t[1])
        _CACHE.update(at=now, rows=rows)
        return list(rows)
    except Exception as e:
        _log.debug("could not read the local inventory: %s", e)
        return list(_CACHE["rows"])


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
    pick = text_only[-1][0] if role in _WANTS_LARGE else text_only[0][0]
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
