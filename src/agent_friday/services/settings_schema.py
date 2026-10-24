"""The settings schema (north star §34.2-13).

``resources/settings.schema.json`` is the shape of ``settings.json``: a type for
every key ``DEFAULT_SETTINGS`` defines (recursively for nested blocks), and the
choices and ranges that ``POST /api/settings`` enforces, taken from the very
constants those checks use (``CONSTRAINTS``). It is checked in, so a new key,
a key whose type changes or a choice list that moves shows up as a schema diff
in review rather than as a row that reads one value while Friday uses another.

    python scripts/gen_settings_schema.py          # rewrite it after a change
    python scripts/gen_settings_schema.py --check  # fail if it is stale

``validate(settings)`` checks a settings dict against it and returns every
problem as a sentence; tests/unit/test_settings_schema.py runs it over the
defaults, a fresh install's first settings, old settings files after their
migrations, and the POST validators' own choices.
"""
from __future__ import annotations

import json
from pathlib import Path

SCHEMA_FILE = Path(__file__).resolve().parents[1] / "resources" / "settings.schema.json"
SCHEMA_ID = "agent-friday/settings"

#: Settings whose stored default is deliberately outside the choices a person
#: can write: the default means "not chosen yet". Each is listed by name, so a
#: default that drifts out of its choices is a failure, not a new allowance.
UNSET_DEFAULTS = {"chat_tool_routing": ""}


def _type_of(value) -> str | None:
    if value is None:
        return None                      # a default of null constrains nothing
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return None


def _node(value) -> dict:
    t = _type_of(value)
    node: dict = {} if t is None else {"type": t}
    if t == "object" and value:
        node["properties"] = {k: _node(v) for k, v in value.items()}
    return node


def constraints() -> dict:
    """Dotted key -> {enum | minimum/maximum/integer | pattern}, from the
    constants the POST /api/settings checks use, so the two cannot drift."""
    from agent_friday.services import owner_choices as oc
    from agent_friday.services import background_gate as bg
    from agent_friday.services import cloud_intent as ci
    from agent_friday.routes import core_routes as cr
    out = {
        "email_cloud_release": {"enum": list(oc.EMAIL_RELEASE_CHOICES)},
        "schedules.missed_default": {"enum": list(oc.MISSED_CHOICES)},
        "schedules.held_default": {"enum": list(oc.HELD_CHOICES)},
        "dock_by_asking": {"enum": list(oc.DOCK_CHOICES)},
        "unattended_send_undo_s": {"minimum": oc.UNDO_MIN_S, "maximum": oc.UNDO_MAX_S},
        "background_idle_minutes": {"integer": True, "minimum": bg.MIN_IDLE_MINUTES,
                                    "maximum": bg.MAX_IDLE_MINUTES},
        "interruption_budget_per_hour": {"integer": True, "minimum": 0, "maximum": 60},
        "quiet_hours_start": {"pattern": r"^([01][0-9]|2[0-3]):[0-5][0-9]$"},
        "quiet_hours_end": {"pattern": r"^([01][0-9]|2[0-3]):[0-5][0-9]$"},
        # Not checked by POST /api/settings itself: the mode control offers
        # exactly these and core._save_settings moves the mode only on an
        # owner routing change.
        "model_routing.mode": {"enum": list(ci.MODES)},
    }
    for k, (_d, lo, hi) in oc.TRUST_SCORING.items():
        out["trust_scoring." + k] = {"minimum": lo, "maximum": hi}
    for key, allowed in cr._VOICE_ENUMS.items():
        if key == "voice_engine":
            continue                     # a cloud voice engine id is also accepted
        out[key] = {"enum": list(allowed)}
    return out


def build() -> dict:
    """The schema from DEFAULT_SETTINGS plus CONSTRAINTS (what the file holds)."""
    from agent_friday.core import DEFAULT_SETTINGS
    root = {"$id": SCHEMA_ID, "type": "object",
            "properties": {k: _node(v) for k, v in DEFAULT_SETTINGS.items()}}
    for dotted, extra in sorted(constraints().items()):
        node, default = root, DEFAULT_SETTINGS
        for part in dotted.split("."):
            node = node.setdefault("properties", {}).setdefault(part, {})
            default = default.get(part) if isinstance(default, dict) else None
        extra = dict(extra)
        if ("enum" in extra and dotted in UNSET_DEFAULTS and default == UNSET_DEFAULTS[dotted]
                and default not in extra["enum"]):
            # "Not chosen yet": a stored value, never a written one.
            extra["enum"] = list(extra["enum"]) + [default]
        node.update(extra)
    return root


def load() -> dict:
    return json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))


def dumps(schema: dict) -> str:
    return json.dumps(schema, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def _check(value, node: dict, where: str, out: list) -> None:
    t = node.get("type")
    if t == "boolean" and not isinstance(value, bool):
        out.append("%s should be true or false (got %r)" % (where, value))
        return
    if t == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))):
        out.append("%s should be a number (got %r)" % (where, value))
        return
    if t == "string" and not isinstance(value, str):
        out.append("%s should be text (got %r)" % (where, value))
        return
    if t == "array" and not isinstance(value, list):
        out.append("%s should be a list (got %r)" % (where, type(value).__name__))
        return
    if t == "object" and not isinstance(value, dict):
        out.append("%s should be a block of settings (got %r)" % (where, type(value).__name__))
        return
    if "enum" in node and value not in node["enum"]:
        out.append("%s is one of %s (got %r)" % (where, ", ".join(map(str, node["enum"])), value))
    if node.get("integer") and (isinstance(value, bool) or not isinstance(value, int)):
        out.append("%s should be a whole number (got %r)" % (where, value))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in node and value < node["minimum"]:
            out.append("%s is at least %s (got %r)" % (where, node["minimum"], value))
        if "maximum" in node and value > node["maximum"]:
            out.append("%s is at most %s (got %r)" % (where, node["maximum"], value))
    if "pattern" in node and isinstance(value, str):
        import re
        if not re.match(node["pattern"], value):
            out.append("%s does not look right (got %r)" % (where, value))
    if isinstance(value, dict):
        for k, sub in (node.get("properties") or {}).items():
            if k in value:
                _check(value[k], sub, "%s.%s" % (where, k), out)


def validate(settings: dict, schema: dict | None = None, *, require_all: bool = False) -> list:
    """Every way `settings` breaks the schema, as sentences. Keys the schema
    does not name are reported only as unknown top-level keys; with
    `require_all`, a missing top-level key is a problem too."""
    schema = schema or load()
    props = schema.get("properties") or {}
    out: list = []
    if not isinstance(settings, dict):
        return ["settings should be a block of settings"]
    for k, v in settings.items():
        if k not in props:
            out.append("%s is not a setting Friday has" % k)
            continue
        _check(v, props[k], k, out)
    if require_all:
        out += ["%s is missing" % k for k in props if k not in settings]
    return out
