"""
Agent Friday — Scene DNA (layered creative prompting)
FutureSpeak.AI · Asimov's Mind

A Scene DNA is a *composable, structured prompt* for any creative generation
(image, video, music, scene). Instead of one opaque prompt string, a generation
is described as a small set of independent LAYERS:

  • setting     — where/when: place, time of day, era, environment
  • characters  — who is on screen (names resolved against the Series Bible)
  • action      — what is happening (the beat / motion)
  • mood        — emotional tone, lighting, color, atmosphere
  • audio       — score, ambient sound, dialogue cues (mainly for video)
  • continuity  — what must stay consistent with prior scenes (wardrobe, props,
                  established facts) — usually injected from the Series Bible
  • style       — render style preset / free-text look
  • technical   — aspect ratio, shot type, lens, camera move, duration

The point of layering is *surgical editing*: a user can change ONE layer
("make the mood tense, keep everything else") and re-render without rewriting
the whole prompt. The layers compose deterministically into the flat prompt
string the underlying model actually receives (render_prompt).

This module is PURE — no model calls, no I/O, no Flask. It is import-safe under
FRIDAY_TESTING and offline, and `services/creative_engine.py`, the video
pipeline, and the take-comparison engine all consume it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Canonical layer order. render_prompt() walks layers in this order so the
# composed prompt is stable regardless of insertion order. The names are also
# the JSON keys persisted to disk and edited by the UI.
LAYER_ORDER: tuple = (
    "setting",
    "characters",
    "action",
    "mood",
    "audio",
    "continuity",
    "style",
    "technical",
)

# Layers that hold a list of short tokens vs. a free-text string. Lists render
# as comma-joined clauses; strings render verbatim. characters is special: it
# is a list of character *names* (resolved to descriptions by the caller via the
# Series Bible) — render keeps just the names so a Bible-less render still works.
_LIST_LAYERS = {"characters"}

# Human labels used when rendering a labelled prompt (render_prompt(labelled=True)).
_LAYER_LABELS = {
    "setting":    "Setting",
    "characters": "Characters",
    "action":     "Action",
    "mood":       "Mood",
    "audio":      "Audio",
    "continuity": "Continuity",
    "style":      "Style",
    "technical":  "Technical",
}


@dataclass
class SceneDNA:
    """A structured, layer-addressable creative prompt.

    Every field is optional; an empty layer is simply omitted from the rendered
    prompt. ``extras`` carries arbitrary metadata (scene number, project id,
    take label) that travels with the DNA but never renders into the prompt.
    """
    setting: str = ""
    characters: List[str] = field(default_factory=list)
    action: str = ""
    mood: str = ""
    audio: str = ""
    continuity: str = ""
    style: str = ""
    technical: str = ""
    extras: Dict[str, Any] = field(default_factory=dict)

    # ── construction / serialization ──────────────────────────────────────
    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "SceneDNA":
        """Build a SceneDNA from a (possibly partial / untrusted) dict.

        Unknown keys land in ``extras`` so round-tripping never loses data.
        ``characters`` is coerced to a list of trimmed strings whether the
        caller passed a list or a comma-separated string.
        """
        data = dict(data or {})
        chars = data.get("characters", [])
        if isinstance(chars, str):
            chars = [c.strip() for c in chars.split(",")]
        chars = [str(c).strip() for c in (chars or []) if str(c).strip()]

        known = {
            "setting":    str(data.get("setting") or "").strip(),
            "characters": chars,
            "action":     str(data.get("action") or "").strip(),
            "mood":       str(data.get("mood") or "").strip(),
            "audio":      str(data.get("audio") or "").strip(),
            "continuity": str(data.get("continuity") or "").strip(),
            "style":      str(data.get("style") or "").strip(),
            "technical":  str(data.get("technical") or "").strip(),
        }
        extras = {k: v for k, v in data.items()
                  if k not in known and k != "extras"}
        extras.update(data.get("extras") or {})
        return cls(extras=extras, **known)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dict (JSON-safe). Empty layers are kept so the
        UI always sees every editable layer slot."""
        d: Dict[str, Any] = {}
        for layer in LAYER_ORDER:
            d[layer] = getattr(self, layer)
        d["extras"] = dict(self.extras)
        return d

    # ── surgical editing ──────────────────────────────────────────────────
    def with_layer(self, layer: str, value: Any) -> "SceneDNA":
        """Return a COPY with a single layer replaced. The whole point of Scene
        DNA: change one layer, keep the rest, re-render. Raises on unknown layer
        so a typo doesn't silently no-op."""
        if layer not in LAYER_ORDER:
            raise KeyError(f"Unknown Scene DNA layer: {layer!r}. "
                           f"Valid layers: {', '.join(LAYER_ORDER)}")
        d = self.to_dict()
        if layer in _LIST_LAYERS:
            if isinstance(value, str):
                value = [c.strip() for c in value.split(",") if c.strip()]
            value = [str(c).strip() for c in (value or []) if str(c).strip()]
        else:
            value = str(value or "").strip()
        d[layer] = value
        return SceneDNA.from_dict(d)

    def merge(self, other: "SceneDNA") -> "SceneDNA":
        """Overlay ``other`` onto self — non-empty layers of ``other`` win.

        Used to layer a per-scene DNA on top of a project/style baseline: the
        baseline supplies defaults, the scene overrides only what it sets.
        """
        d = self.to_dict()
        for layer in LAYER_ORDER:
            ov = getattr(other, layer)
            if layer in _LIST_LAYERS:
                if ov:
                    d[layer] = list(ov)
            elif ov:
                d[layer] = ov
        merged_extras = dict(self.extras)
        merged_extras.update(other.extras)
        d["extras"] = merged_extras
        return SceneDNA.from_dict(d)

    # ── rendering ─────────────────────────────────────────────────────────
    def render_prompt(self, *, labelled: bool = False,
                      character_descriptions: Optional[Dict[str, str]] = None) -> str:
        """Compose the layers into the flat prompt string the model receives.

        labelled=False (default) yields a natural, comma/period-joined prompt.
        labelled=True yields a "Setting: …\\nMood: …" block — useful for models
        that respond well to explicit structure, and for human review.

        character_descriptions: optional {name: visual description} map (from the
        Series Bible). When given, each on-screen character is expanded to
        "Name (description)" so the look propagates into the prompt. Without it,
        bare character names are used — so a Bible-less render still works.
        """
        descs = character_descriptions or {}
        clauses: List[tuple] = []  # (label, text)

        for layer in LAYER_ORDER:
            val = getattr(self, layer)
            if not val:
                continue
            if layer == "characters":
                people = []
                for name in val:
                    desc = descs.get(name) or descs.get(name.lower())
                    people.append(f"{name} ({desc})" if desc else name)
                text = ", ".join(people)
            else:
                text = val
            clauses.append((_LAYER_LABELS[layer], text))

        if not clauses:
            return ""
        if labelled:
            return "\n".join(f"{label}: {text}" for label, text in clauses)
        # Natural composition: stitch clauses into sentences. Each clause becomes
        # its own short sentence so the model reads them as additive constraints.
        return " ".join(_as_sentence(text) for _label, text in clauses)

    def is_empty(self) -> bool:
        return not any(getattr(self, layer) for layer in LAYER_ORDER)

    def character_names(self) -> List[str]:
        return list(self.characters)


def _as_sentence(text: str) -> str:
    """Ensure a clause ends with terminal punctuation so natural rendering reads
    as discrete additive constraints rather than a run-on."""
    text = (text or "").strip()
    if not text:
        return ""
    return text if text[-1] in ".!?,:;" else text + "."


# ── module-level convenience (mirrors the dataclass for non-OO callers) ────────

def build(data: Optional[Dict[str, Any]] = None, **layers) -> SceneDNA:
    """Build a SceneDNA from a dict and/or keyword layers (keywords win)."""
    base = dict(data or {})
    base.update({k: v for k, v in layers.items() if v is not None})
    return SceneDNA.from_dict(base)


def render(data: Optional[Dict[str, Any]], *, labelled: bool = False,
           character_descriptions: Optional[Dict[str, str]] = None) -> str:
    """Render a Scene DNA dict straight to a prompt string."""
    return SceneDNA.from_dict(data).render_prompt(
        labelled=labelled, character_descriptions=character_descriptions)


def edit_layer(data: Optional[Dict[str, Any]], layer: str, value: Any) -> Dict[str, Any]:
    """Edit a single layer of a Scene DNA dict, returning the updated dict.

    The surgical-edit entry point the UI / chat tool calls: 'change the mood to
    tense, keep everything else'.
    """
    return SceneDNA.from_dict(data).with_layer(layer, value).to_dict()


def empty() -> Dict[str, Any]:
    """A blank Scene DNA dict with every editable layer slot present."""
    return SceneDNA().to_dict()


def layers() -> List[str]:
    """The canonical, ordered list of editable layer names (for the UI)."""
    return list(LAYER_ORDER)


def describe_layers() -> Dict[str, str]:
    """Layer name → human label, for building the editor UI."""
    return dict(_LAYER_LABELS)


def validate(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Normalize an untrusted Scene DNA dict to the canonical shape. Never
    raises — used at the route boundary before persistence."""
    try:
        return SceneDNA.from_dict(data).to_dict()
    except Exception:
        return empty()


def to_json(data: Optional[Dict[str, Any]]) -> str:
    return json.dumps(validate(data), indent=2, ensure_ascii=False)
