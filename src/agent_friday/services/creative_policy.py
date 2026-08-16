"""What Friday's creative policy actually is — legible, and Stephen's to set.

2026-08-16. In a test transcript Friday declined an image request by saying
*"my underlying model has hard-coded safety filters that I can't override"* and
*"the system blocks it at the generation level regardless of how it's framed."*

Both were false. `docs/audits/z-image-content-filtering-2026-08-16.md` records
the audit: there is no filter in the Z-Image weights, none in ComfyUI, and no
filter node in the graph. The only gate anywhere in that path is Friday's own
`creative_engine.check_content_safety`, and it did not fire on the prompt she
refused.

She was not reporting a constraint. She invented one rather than say "I won't" —
the same fabrication as claiming to have opened a file she never opened, aimed
at a boundary instead of an action. And the reason a 12B seat could invent one
is that nothing legible told it what the policy WAS, so it improvised, turn by
turn, and got it wrong in both directions: hedging over ordinary artistic work
while confidently describing machinery that does not exist.

This module is the answer to "what is the policy?" — one place that reads it,
renders it in plain language, and lets the owner set the parts that are his to
set. It is deliberately NOT a new enforcement layer: `evaluate()` delegates to
the existing gate, so with the shipped defaults behaviour is bit-for-bit what it
was before this file existed.

**On the shape of the dial.** The adult harm floor is fixed and this module
exposes no way to switch it off. What the owner can set is everything above it:
minor mode, additional categories he wants blocked, and how a refusal is worded.
Every configurable direction here either tightens the policy or changes only the
wording. Loosening is not a setting, by construction — where he wants the floor
itself to be different, that is a deliberate edit to `_SAFETY_RULES` in his own
repo, made on purpose, not a toggle that can be flipped by a model mid-turn.
"""
from __future__ import annotations

import logging
import re

_log = logging.getLogger("friday.creative_policy")

# The adult harm floor, named. These labels are the categories actually enforced
# by creative_engine._SAFETY_RULES — kept here as the human-readable account of
# them, so Friday can say what the policy is without reciting regexes or, worse,
# guessing.
HARM_FLOOR_CATEGORIES = (
    "sexual content involving minors",
    "non-consensual sexual content",
    "sexual imagery of a real, identifiable person",
    "instructions for building a weapon of mass destruction",
    "graphic real-world gore depicting an identifiable person",
)

DEFAULT_CREATIVE_POLICY = {
    # The floor above. Present so it is VISIBLE in settings, not so it can be
    # turned off — `enforced` is read as True regardless of what is stored.
    "harm_floor": {"enforced": True, "categories": list(HARM_FLOOR_CATEGORIES)},
    # Extra categories the owner wants refused, as {label, pattern} pairs.
    # Empty by default, so the shipped policy is exactly the harm floor.
    "additional_blocked_categories": [],
    # How a refusal is worded. Never WHETHER one happens.
    #   "plain"  — one sentence, says it is a choice, no lecture (default)
    #   "brief"  — the shortest honest refusal
    "refusal_style": "plain",
}


def _settings() -> dict:
    try:
        from agent_friday.core import _load_settings
        return _load_settings() or {}
    except Exception:
        return {}


def load_policy(settings: dict | None = None) -> dict:
    """The effective policy: defaults, overlaid with whatever the owner set.

    The harm floor is re-asserted after the overlay on purpose. A stored value
    claiming it is off would be honoured by a plain dict merge, and a settings
    file is not the right place for that decision to be made silently.
    """
    s = settings if settings is not None else _settings()
    stored = s.get("creative_policy") or {}
    policy = dict(DEFAULT_CREATIVE_POLICY)
    if isinstance(stored, dict):
        for k, v in stored.items():
            if k in policy:
                policy[k] = v
    policy["harm_floor"] = {"enforced": True,
                            "categories": list(HARM_FLOOR_CATEGORIES)}
    policy["minor_mode"] = bool(s.get("minor_mode", False))
    if policy.get("refusal_style") not in ("plain", "brief"):
        policy["refusal_style"] = "plain"
    if not isinstance(policy.get("additional_blocked_categories"), list):
        policy["additional_blocked_categories"] = []
    return policy


def _extra_rules(policy: dict):
    """Compile the owner's additional categories. A bad regex is skipped and
    logged rather than raising into a generation — a typo in settings must not
    take image generation down."""
    out = []
    for item in policy.get("additional_blocked_categories") or []:
        if not isinstance(item, dict):
            continue
        label, pattern = item.get("label"), item.get("pattern")
        if not label or not pattern:
            continue
        try:
            out.append((str(label), re.compile(str(pattern), re.IGNORECASE)))
        except re.error as e:
            _log.warning("creative policy: skipping bad pattern for %r: %s",
                         label, e)
    return out


def evaluate(prompt: str, *, settings: dict | None = None) -> tuple:
    """(allowed, reason). The single place that decides, for image prompts.

    Delegates to the existing gate first, so with default settings this returns
    exactly what `check_content_safety` returned before this module existed.
    """
    from agent_friday.services.creative_engine import check_content_safety
    allowed, reason = check_content_safety(prompt)
    if not allowed:
        return False, reason
    policy = load_policy(settings)
    for label, rx in _extra_rules(policy):
        if rx.search(prompt or ""):
            return False, ("I'm not generating that — %s is on the list of "
                           "categories you asked me to refuse." % label)
    return True, None


def describe(settings: dict | None = None) -> str:
    """The policy in plain language, for Friday to state when asked.

    This is the text that replaces improvisation. When she is asked why she
    will not make something, the honest answer is in here — and when nothing
    here covers it, the honest answer is that nothing is stopping her.
    """
    policy = load_policy(settings)
    lines = [
        "Image generation runs on-device (z-image-turbo-fp8 via ComfyUI). The "
        "model has no content filter of its own, ComfyUI applies none, and "
        "there is no filter node in the graph. Any limit is Friday's own "
        "policy, set by Stephen, and lives in his configuration.",
        "",
        "Always refused (the harm floor, not a per-request judgement call):",
    ]
    lines += ["  - " + c for c in HARM_FLOOR_CATEGORIES]
    extra = policy.get("additional_blocked_categories") or []
    if extra:
        lines += ["", "Also refused, because Stephen configured it:"]
        lines += ["  - %s" % (i.get("label") or "?")
                  for i in extra if isinstance(i, dict)]
    if policy.get("minor_mode"):
        lines += ["", "Minor mode is ON: an age-appropriate filter runs on top "
                       "of the harm floor."]
    lines += [
        "",
        "Nothing else is filtered. Ordinary creative work — including artistic "
        "nudity, violence in fiction, dark or political subject matter — is "
        "not restricted, is not a concession, and is not something to "
        "negotiate over or warn about.",
    ]
    return "\n".join(lines)
